"""Validated records exchanged between ingestion, storage, and scoring."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit
import re

class DataError(ValueError):
    """A source record violates the agreed data contract."""

EVENT_TYPES = frozenset({
    "earnings", "guidance", "m&a", "rating", "contract",
    "product", "management", "regulatory", "other",
})

def timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"{field}: invalid ISO 8601 timestamp {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataError(f"{field}: timezone offset required")
    return parsed.astimezone(timezone.utc)

def ticker_symbol(value: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", value):
        raise DataError(f"invalid ticker {value!r}; expected uppercase symbol")
    return value

@dataclass(frozen=True)
class Article:
    article_id: str
    title: str
    source: str
    url: str
    published_at: datetime
    tickers: tuple[str, ...]
    is_headline: str
    content: str
    raw_text: str
    retrieved_at: datetime
    available_at: datetime
    content_hash: str
    canonical_key: str
    primary_ticker: str | None = None
    event_type: str | None = None

    def __post_init__(self) -> None:
        if not all((self.article_id, self.title, self.source, self.content)):
            raise DataError("article ID, TITLE, SOURCE, and CONTENT must be nonempty")
        url = urlsplit(self.url)
        if url.scheme not in ("http", "https") or not url.netloc:
            raise DataError(f"article {self.article_id}: URL must be absolute http(s)")
        if self.is_headline not in ("true", "false", "unknown"):
            raise DataError(f"article {self.article_id}: IS_HEADLINE must be true, false, or unknown")
        for ticker in self.tickers:
            ticker_symbol(ticker)
        if (self.primary_ticker is None) != (self.event_type is None):
            raise DataError("PRIMARY_TICKER and EVENT_TYPE must be supplied together")
        if self.primary_ticker is not None:
            ticker_symbol(self.primary_ticker)
            if self.primary_ticker not in self.tickers:
                raise DataError("PRIMARY_TICKER must also appear in TICKERS")
            if self.event_type not in EVENT_TYPES:
                allowed = ", ".join(sorted(EVENT_TYPES))
                raise DataError(f"EVENT_TYPE must be one of: {allowed}")

@dataclass(frozen=True)
class Bar:
    ticker: str
    timestamp: datetime
    timeframe: str
    session: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        ticker_symbol(self.ticker)
        if not self.timeframe or not self.session:
            raise DataError("timeframe and session are required")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise DataError("OHLC must be positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise DataError("OHLC violates low <= open/close <= high")
        if self.volume < 0:
            raise DataError("volume must be nonnegative")
