"""Deterministic daily news scores; Qdrant is never used for frequency counts."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from math import log1p
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import DataError, ticker_symbol
from .storage import articles_as_of

NEWS_FEATURES = ("article_mentions", "source_breadth", "verified_headlines")


def news_universe(path: str | Path) -> list[str]:
    """Read the configured stock universe independently of market-bar coverage."""
    tickers = [ticker_symbol(line.strip()) for line in Path(path).read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if not tickers or len(tickers) != len(set(tickers)):
        raise DataError("news universe must contain unique ticker symbols")
    return tickers


def news_window(as_of: datetime, days: int, timezone_name: str) -> tuple[list[date], datetime]:
    """Return local calendar dates and the UTC beginning of their shared window."""
    if as_of.tzinfo is None or days < 1:
        raise DataError("news cutoff must be timezone-aware and news_window_days must be positive")
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise DataError(f"timezone {timezone_name!r} is unavailable; install tzdata on Windows") from exc
    last_day = as_of.astimezone(local_zone).date()
    dates = [last_day - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    first = datetime.combine(dates[0], time.min, local_zone).astimezone(timezone.utc)
    return dates, first


def score_daily_news(db, as_of: datetime, universe: list[str], config: dict) -> dict:
    """Score every universe ticker on each day, then average seven daily scores.

    A daily slice is a snapshot *as known at as_of*. An article discovered later
    cannot appear in an earlier run, even when its publication date is earlier.
    """
    days = config.get("news_window_days", 7)
    if not isinstance(days, int) or isinstance(days, bool):
        raise DataError("news_window_days must be an integer")
    zone_name = config.get("news_timezone", "America/New_York")
    dates, window_start = news_window(as_of, days, zone_name)
    weights = config.get("news_feature_weights", {field: 1 / 3 for field in NEWS_FEATURES})
    if set(weights) != set(NEWS_FEATURES) or any(not isinstance(value, (int, float)) or value < 0 for value in weights.values()) or abs(sum(weights.values()) - 1) > 1e-9:
        raise DataError("news_feature_weights must be nonnegative and sum to 1")
    zone = ZoneInfo(zone_name)
    tickers = sorted(set(universe))
    observed = set(tickers)
    grouped: dict[tuple[str, date], list[dict]] = defaultdict(list)
    rows = articles_as_of(db, window_start, as_of)
    for row in rows:
        if row["ticker"] in observed:
            day = datetime.fromisoformat(row["published_at"]).astimezone(zone).date()
            if day in dates:
                grouped[row["ticker"], day].append(row)

    daily: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
    for day in dates:
        counts: dict[str, dict] = {}
        for ticker in tickers:
            items = grouped[ticker, day]
            counts[ticker] = {
                "article_mentions": len(items),
                "source_breadth": len({item["source"] for item in items}),
                "verified_headlines": sum(item["is_headline"] == "true" for item in items),
                "evidence_ids": [item["article_id"] for item in items],
            }
        maxima = {field: max((counts[ticker][field] for ticker in tickers), default=0)
                  for field in NEWS_FEATURES}
        for ticker in tickers:
            values = counts[ticker]
            # Log scaling preserves zero while limiting a single prolific ticker's influence.
            normalized = {field: log1p(values[field]) / log1p(maxima[field]) if maxima[field] else 0.0
                          for field in NEWS_FEATURES}
            score = sum(weights[field] * normalized[field] for field in NEWS_FEATURES)
            daily[ticker].append({"date": day.isoformat(), **values,
                                  "normalized": normalized, "score": score})

    return {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "timezone": zone_name,
        "window_start_inclusive": window_start.isoformat(),
        "window_end_inclusive": as_of.astimezone(timezone.utc).isoformat(),
        "dates": [day.isoformat() for day in dates],
        "news_feature_weights": weights,
        "scores": {ticker: {"news_score": sum(item["score"] for item in items) / days,
                            "daily": items} for ticker, items in daily.items()},
    }
