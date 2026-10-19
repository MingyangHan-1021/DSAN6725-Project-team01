"""Strict TXT/CSV parsing; errors identify the offending record or line."""
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .models import Article, Bar, DataError, timestamp, ticker_symbol

REQUIRED_NEWS_FIELDS = ("ID", "TITLE", "SOURCE", "URL", "PUBLISHED_AT", "TICKERS", "IS_HEADLINE")
OPTIONAL_NEWS_FIELDS = ("PRIMARY_TICKER", "EVENT_TYPE")
NEWS_FIELDS = REQUIRED_NEWS_FIELDS + OPTIONAL_NEWS_FIELDS
MARKET_FIELDS = ("ticker", "timestamp", "timeframe", "session", "open", "high", "low", "close", "volume")

def _parse_article(lines: list[str], index: int, retrieved_at: datetime) -> Article:
    fields: dict[str, str] = {}
    body: list[str] = []
    in_body = False
    for line in lines:
        if in_body:
            body.append(line)
        elif line.strip() == "CONTENT:":
            in_body = True
        else:
            if ":" not in line:
                raise DataError(f"article {index}: expected FIELD: value, got {line!r}")
            key, value = line.split(":", 1)
            if key not in NEWS_FIELDS or key in fields:
                raise DataError(f"article {index}: unknown or repeated field {key!r}")
            fields[key] = value.strip()
    missing = [key for key in REQUIRED_NEWS_FIELDS if key not in fields]
    if missing or not in_body:
        raise DataError(f"article {index}: missing {missing} or CONTENT: marker")
    published = timestamp(fields["PUBLISHED_AT"], f"article {index} PUBLISHED_AT")
    if retrieved_at < published:
        raise DataError(f"article {index}: retrieval precedes publication")
    content = "\n".join(body).strip()
    tickers = tuple(dict.fromkeys(ticker_symbol(x.strip()) for x in fields["TICKERS"].split(",") if x.strip()))
    primary_ticker = fields.get("PRIMARY_TICKER") or None
    event_type = fields.get("EVENT_TYPE") or None
    parts = urlsplit(fields["URL"])
    canonical_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))
    content_hash = sha256((fields["TITLE"].casefold() + "\n" + content.casefold()).encode()).hexdigest()
    # The URL and content hash are kept separately; either can expose duplicate coverage.
    canonical_key = sha256(canonical_url.encode()).hexdigest() if parts.scheme and parts.netloc else content_hash
    return Article(fields["ID"], fields["TITLE"], fields["SOURCE"], fields["URL"], published,
                   tickers, fields["IS_HEADLINE"], content,
                   "=== ARTICLE ===\n" + "\n".join(lines) + "\n=== END ===",
                   retrieved_at, retrieved_at, content_hash, canonical_key,
                   primary_ticker=primary_ticker, event_type=event_type)

def parse_news(path: str | Path, retrieved_at: datetime | None = None) -> list[Article]:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    if retrieved_at.tzinfo is None:
        raise DataError("retrieved_at requires timezone")
    retrieved_at = retrieved_at.astimezone(timezone.utc)
    records: list[Article] = []
    inside = False
    lines: list[str] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        marker = line.strip()
        if marker == "=== ARTICLE ===":
            if inside:
                raise DataError(f"news line {number}: nested ARTICLE marker")
            inside, lines = True, []
        elif marker == "=== END ===":
            if not inside:
                raise DataError(f"news line {number}: END without ARTICLE")
            records.append(_parse_article(lines, len(records) + 1, retrieved_at))
            inside = False
        elif inside:
            lines.append(line)
        elif marker:
            raise DataError(f"news line {number}: text outside record")
    if inside:
        raise DataError("news file ends before === END ===")
    if len({record.article_id for record in records}) != len(records):
        raise DataError("duplicate article ID in news file")
    return records

def parse_market(path: str | Path) -> list[Bar]:
    bars: list[Bar] = []
    seen: set[tuple[str, datetime, str, str]] = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(MARKET_FIELDS):
            raise DataError(f"market CSV columns must be exactly {','.join(MARKET_FIELDS)}")
        for line_number, row in enumerate(reader, 2):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise DataError("wrong number of CSV fields")
                prices = [Decimal(row[key]) for key in ("open", "high", "low", "close")]
                if not all(price.is_finite() for price in prices):
                    raise DataError("OHLC must be finite")
                volume = int(row["volume"])
                if str(volume) != row["volume"].strip():
                    raise DataError("volume must be an integer")
                bar = Bar(ticker_symbol(row["ticker"]), timestamp(row["timestamp"], "timestamp"),
                          row["timeframe"], row["session"], *prices, volume)
                key = (bar.ticker, bar.timestamp, bar.timeframe, bar.session)
                if key in seen:
                    raise DataError(f"duplicate bar key {key}")
                seen.add(key)
                bars.append(bar)
            except (DataError, InvalidOperation, ValueError, TypeError) as exc:
                raise DataError(f"market CSV line {line_number}: {exc}") from exc
    return bars
