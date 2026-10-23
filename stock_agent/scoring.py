"""Deterministic features and rank calculation over the observed stock universe."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from .daily_news import news_universe, score_daily_news
from .models import DataError
from .storage import articles_as_of, bars_as_of

FEATURE_VERSION = "agent1-features-v4-daily-news"

def percentile(values: dict[str, float | None]) -> dict[str, float | None]:
    """Tied values share rank; missing values stay missing."""
    present = sorted((value, ticker) for ticker, value in values.items() if value is not None)
    if not present:
        return {ticker: None for ticker in values}
    unique = sorted({value for value, _ in present})
    # A constant feature does not distinguish stocks, even when its common value is positive.
    return {ticker: None if value is None else (unique.index(value) / (len(unique) - 1) if len(unique) > 1 else 0.0)
            for ticker, value in values.items()}

def market_features(rows: list[dict], as_of: datetime, baseline_bars: int) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["ticker"]].append(row)
    output = {}
    for ticker, bars in grouped.items():
        latest = bars[-1]
        current = float(latest["close"])
        prior = bars[-2] if len(bars) > 1 else None
        cutoff = datetime.fromisoformat(latest["timestamp"]) - timedelta(days=7)
        week_prior = next((bar for bar in reversed(bars[:-1]) if datetime.fromisoformat(bar["timestamp"]) <= cutoff), None)
        history = bars[-(baseline_bars + 1):-1]
        volume_mean = mean(bar["volume"] for bar in history) if len(history) == baseline_bars else None
        ranges = [(float(bar["high"]) - float(bar["low"])) / float(bar["open"]) for bar in history]
        current_range = (float(latest["high"]) - float(latest["low"])) / float(latest["open"])
        output[ticker] = {
            "latest_bar_at": latest["timestamp"], "latest_close": current, "latest_volume": latest["volume"],
            "abs_1d_return": abs(current / float(prior["close"]) - 1) if prior else None,
            "abs_7d_move": abs(current / float(week_prior["close"]) - 1) if week_prior else None,
            "relative_volume": latest["volume"] / volume_mean if volume_mean and volume_mean > 0 else None,
            "volatility_anomaly": current_range / mean(ranges) if len(ranges) == baseline_bars and mean(ranges) > 0 else None,
        }
    return output

def news_features(rows: list[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["ticker"]].append(row)
    output = {}
    for ticker, items in grouped.items():
        # Every supplied record counts as a mention, including repeated coverage from one source.
        # Keep a separate deduplicated count so callers can inspect repeated coverage.
        unique: list[dict] = []
        urls: set[str] = set()
        hashes: set[str] = set()
        for item in items:
            if item["canonical_key"] in urls or item["content_hash"] in hashes:
                continue
            urls.add(item["canonical_key"])
            hashes.add(item["content_hash"])
            unique.append(item)
        output[ticker] = {
            "article_mentions": len(items),
            "unique_articles": len(unique),
            "source_breadth": len({item["source"] for item in items}),
            "verified_headlines": sum(item["is_headline"] == "true" for item in items),
            "evidence_ids": [item["article_id"] for item in items],
            "coverage_records": len(items),
            "unique_events": None,  # Article deduplication does not establish event identity.
        }
    return output

def rank(db, as_of: datetime, config: dict, top_k: int = 10) -> dict:
    if top_k < 1:
        raise DataError("top_k must be positive")
    if as_of.tzinfo is None:
        raise DataError("as_of requires timezone")
    as_of = as_of.astimezone(timezone.utc)
    weights = config["weights"]
    if min(weights.values()) < 0 or abs(weights["market"] + weights["news"] - 1) > 1e-9:
        raise DataError("market and news weights must be nonnegative and sum to 1")
    if config["volume_baseline_bars"] < 1 or config["max_market_age_days"] < 0:
        raise DataError("volume baseline must be positive and maximum market age nonnegative")
    market = market_features(bars_as_of(db, as_of, config["timeframe"], config["session"]),
                             as_of, config["volume_baseline_bars"])
    # Score the configured universe even when a ticker has no market bar or article.
    daily_news = score_daily_news(db, as_of,
        news_universe(config.get("news_universe_file", "config/sp500_tickers.txt")) + list(market), config)
    window_start = datetime.fromisoformat(daily_news["window_start_inclusive"])
    news = news_features(articles_as_of(db, window_start, as_of))
    # A ticker needs an observed matching market bar. News-only tickers remain stored for research.
    universe = sorted(ticker for ticker, data in market.items()
                      if data["latest_close"] >= config["min_price"] and data["latest_volume"] >= config["min_volume"]
                      and datetime.fromisoformat(data["latest_bar_at"]) >= as_of - timedelta(days=config["max_market_age_days"]))
    market_fields = ("abs_1d_return", "abs_7d_move", "relative_volume", "volatility_anomaly")
    normalized = {field: percentile({ticker: market[ticker][field] for ticker in universe}) for field in market_fields}
    ranked = []
    for ticker in universe:
        available = [normalized[field][ticker] for field in market_fields if normalized[field][ticker] is not None]
        market_score = mean(available) if available else None
        news_score = daily_news["scores"][ticker]["news_score"]
        news_data = news.get(ticker, {"article_mentions": 0, "unique_articles": 0, "source_breadth": 0,
                                     "verified_headlines": 0, "evidence_ids": [], "coverage_records": 0,
                                     "unique_events": None})
        signals = {field: market[ticker][field] for field in market_fields}
        signals.update({field: news_data[field] for field in ("article_mentions", "source_breadth", "verified_headlines")})
        signals["unique_articles"] = news_data["unique_articles"]
        ranked.append({"ticker": ticker, "market_score": market_score, "news_score": news_score,
                       "hotness_score": weights["market"] * (market_score or 0) + weights["news"] * news_score,
                       "main_signals": signals, "evidence_ids": news_data["evidence_ids"],
                       "daily_news_scores": [{"date": item["date"], "score": item["score"]}
                                             for item in daily_news["scores"][ticker]["daily"]],
                       "missing_market_features": [field for field in market_fields if market[ticker][field] is None],
                       "latest_bar_at": market[ticker]["latest_bar_at"]})
    ranked.sort(key=lambda row: (-row["hotness_score"], row["ticker"]))
    for index, row in enumerate(ranked[:top_k], 1):
        row["rank"] = index
    return {"as_of": as_of.isoformat(), "window_start_inclusive": window_start.isoformat(),
            "window_end_inclusive": as_of.isoformat(), "feature_version": FEATURE_VERSION,
            "news_timezone": daily_news["timezone"], "news_dates": daily_news["dates"],
            "news_score_universe": len(daily_news["scores"]), "news_scores": daily_news["scores"],
            "coverage_note": "Top stocks within supplied market/news data; full US-market coverage is not established.",
            "eligible_tickers": len(universe), "ranked": ranked[:top_k]}
