"""SQLite is the canonical store; Qdrant only mirrors searchable evidence."""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from .models import Article, Bar, DataError

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS articles (
 article_id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL,
 published_at TEXT NOT NULL, retrieved_at TEXT NOT NULL, available_at TEXT NOT NULL,
 is_headline TEXT NOT NULL, content TEXT NOT NULL, raw_text TEXT NOT NULL,
 content_hash TEXT NOT NULL, canonical_key TEXT NOT NULL,
 primary_ticker TEXT, event_type TEXT
);
CREATE TABLE IF NOT EXISTS article_tickers (
 article_id TEXT NOT NULL REFERENCES articles(article_id), ticker TEXT NOT NULL,
 PRIMARY KEY(article_id,ticker)
);
CREATE TABLE IF NOT EXISTS bars (
 ticker TEXT NOT NULL, timestamp TEXT NOT NULL, timeframe TEXT NOT NULL, session TEXT NOT NULL,
 open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL, volume INTEGER NOT NULL,
 available_at TEXT NOT NULL,
 PRIMARY KEY(ticker,timestamp,timeframe,session)
);
CREATE TABLE IF NOT EXISTS rank_runs (
 run_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, created_at TEXT NOT NULL,
 config_json TEXT NOT NULL, output_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS articles_available ON articles(available_at,published_at);
CREATE INDEX IF NOT EXISTS bars_time ON bars(timestamp,timeframe,session);
"""

def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    # Additive migration for databases created before news classification fields existed.
    article_columns = {row[1] for row in db.execute("PRAGMA table_info(articles)")}
    if "primary_ticker" not in article_columns:
        db.execute("ALTER TABLE articles ADD COLUMN primary_ticker TEXT")
    if "event_type" not in article_columns:
        db.execute("ALTER TABLE articles ADD COLUMN event_type TEXT")
    db.commit()
    return db

def ingest(db: sqlite3.Connection, articles: list[Article], bars: list[Bar], market_retrieved_at: datetime) -> dict:
    """Validate conflicts before committing anything; repeat input leaves row counts unchanged."""
    if market_retrieved_at.tzinfo is None:
        raise DataError("market_retrieved_at requires timezone")
    market_retrieved_at = market_retrieved_at.astimezone(timezone.utc)
    with db:
        for article in articles:
            existing = db.execute("SELECT * FROM articles WHERE article_id=?", (article.article_id,)).fetchone()
            if existing:
                # A stable ID cannot silently acquire different provenance or headline status.
                if (existing["title"], existing["source"], existing["url"], existing["published_at"],
                    existing["is_headline"], existing["content"], existing["raw_text"],
                    existing["content_hash"], existing["canonical_key"]) != (
                    article.title, article.source, article.url, article.published_at.isoformat(),
                    article.is_headline, article.content, article.raw_text,
                    article.content_hash, article.canonical_key):
                    raise DataError(f"article ID {article.article_id} conflicts with stored evidence")
                previous = {row[0] for row in db.execute("SELECT ticker FROM article_tickers WHERE article_id=?", (article.article_id,))}
                if previous != set(article.tickers):
                    raise DataError(f"article ID {article.article_id} has changed tickers")
                stored_classification = (existing["primary_ticker"], existing["event_type"])
                new_classification = (article.primary_ticker, article.event_type)
                if stored_classification == (None, None) and new_classification != (None, None):
                    db.execute("UPDATE articles SET primary_ticker=?,event_type=? WHERE article_id=?",
                               new_classification + (article.article_id,))
                elif new_classification != (None, None) and stored_classification != new_classification:
                    raise DataError(f"article ID {article.article_id} has changed classification")
                continue
            db.execute("""INSERT INTO articles (
                article_id,title,source,url,published_at,retrieved_at,available_at,is_headline,
                content,raw_text,content_hash,canonical_key,primary_ticker,event_type
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                article.article_id, article.title, article.source, article.url,
                article.published_at.isoformat(), article.retrieved_at.isoformat(),
                article.available_at.isoformat(), article.is_headline, article.content,
                article.raw_text, article.content_hash, article.canonical_key,
                article.primary_ticker, article.event_type))
            db.executemany("INSERT INTO article_tickers VALUES (?,?)", ((article.article_id, ticker) for ticker in article.tickers))
        for bar in bars:
            if bar.timestamp > market_retrieved_at:
                raise DataError(f"bar {bar.ticker} timestamp is after its retrieval time")
            key = (bar.ticker, bar.timestamp.isoformat(), bar.timeframe, bar.session)
            values = tuple(str(value) for value in (bar.open, bar.high, bar.low, bar.close)) + (bar.volume,)
            existing = db.execute("SELECT open,high,low,close,volume FROM bars WHERE ticker=? AND timestamp=? AND timeframe=? AND session=?", key).fetchone()
            if existing:
                # CSV formatting such as 10 versus 10.0 does not change an OHLCV bar.
                if tuple(Decimal(value) for value in existing[:4]) != (bar.open, bar.high, bar.low, bar.close) or existing[4] != bar.volume:
                    raise DataError(f"bar {key} conflicts with stored OHLCV")
                continue
            db.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?)", key + values + (market_retrieved_at.isoformat(),))
    return {"articles_in_file": len(articles), "bars_in_file": len(bars),
            "articles_total": db.execute("SELECT count(*) FROM articles").fetchone()[0],
            "bars_total": db.execute("SELECT count(*) FROM bars").fetchone()[0]}

def articles_as_of(db: sqlite3.Connection, start: datetime, end: datetime) -> list[dict]:
    """Both publication and first availability must precede the cutoff."""
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    rows = db.execute("""SELECT a.*, t.ticker FROM articles a JOIN article_tickers t USING(article_id)
        WHERE a.published_at>=? AND a.published_at<=? AND a.available_at<=?
        ORDER BY a.article_id,t.ticker""", (start.isoformat(), end.isoformat(), end.isoformat())).fetchall()
    return [dict(row) for row in rows]

def bars_as_of(db: sqlite3.Connection, end: datetime, timeframe: str, session: str) -> list[dict]:
    end = end.astimezone(timezone.utc)
    rows = db.execute("""SELECT * FROM bars WHERE timestamp<=? AND available_at<=?
        AND timeframe=? AND session=? ORDER BY ticker,timestamp""",
        (end.isoformat(), end.isoformat(), timeframe, session)).fetchall()
    return [dict(row) for row in rows]

def save_run(db: sqlite3.Connection, run_id: str, as_of: datetime, created_at: datetime,
             config: dict, output: dict) -> None:
    # A run is immutable. A new rerun gets a new ID and preserves the earlier snapshot.
    with db:
        db.execute("INSERT INTO rank_runs VALUES (?,?,?,?,?)", (run_id, as_of.isoformat(),
                   created_at.isoformat(), json.dumps(config, sort_keys=True), json.dumps(output, sort_keys=True)))
