"""Command line entry point for Agent 1. Run `python main.py --help`."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4
from stock_agent.ingestion import parse_news, parse_market
from stock_agent.memory import QdrantMemory, embedding
from stock_agent.models import DataError, timestamp
from stock_agent.scoring import rank
from stock_agent.storage import connect, ingest, save_run

DEFAULT_DB = "data/agent1.sqlite3"
DEFAULT_CONFIG = "config/scoring.json"

def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent 1: ingest supplied data, rank observed stocks, search evidence")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite path")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="validate and store teammate files")
    ingest_cmd.add_argument("--news", required=True, help="structured news TXT")
    ingest_cmd.add_argument("--market", required=True, help="OHLCV CSV")
    ingest_cmd.add_argument("--retrieved-at", help="shared retrieval time fallback for both files; timezone-aware ISO 8601")
    ingest_cmd.add_argument("--news-retrieved-at", help="actual news retrieval time; overrides --retrieved-at")
    ingest_cmd.add_argument("--market-retrieved-at", help="actual market retrieval time; overrides --retrieved-at")
    rank_cmd = commands.add_parser("rank", help="export deterministic Top 10")
    rank_cmd.add_argument("--as-of", required=True, help="information cutoff; timezone-aware ISO 8601")
    rank_cmd.add_argument("--top-k", type=int, default=10)
    rank_cmd.add_argument("--config", default=DEFAULT_CONFIG)
    rank_cmd.add_argument("--output", help="optional JSON output path")
    for name in ("memory-index", "memory-search"):
        cmd = commands.add_parser(name, help="index or query the optional Qdrant evidence memory")
        cmd.add_argument("--qdrant-url", default="http://localhost:6333")
        cmd.add_argument("--collection", default="agent1_news")
        cmd.add_argument("--embedding-provider", choices=("hash", "sentence-transformers"), default="hash")
        cmd.add_argument("--embedding-model")
        if name == "memory-search":
            cmd.add_argument("--query", required=True)
            cmd.add_argument("--ticker", action="append")
            cmd.add_argument("--published-before")
            cmd.add_argument("--published-after")
            cmd.add_argument("--available-before")
            cmd.add_argument("--limit", type=int, default=10)
    return parser

def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        # sqlite3's context manager commits/rolls back but does not close the connection.
        with closing(connect(args.db)) as db, db:
            if args.command == "ingest":
                shared = timestamp(args.retrieved_at, "retrieved_at") if args.retrieved_at else None
                now = datetime.now(timezone.utc)
                news_retrieved = (timestamp(args.news_retrieved_at, "news_retrieved_at")
                                  if args.news_retrieved_at else shared or now)
                market_retrieved = (timestamp(args.market_retrieved_at, "market_retrieved_at")
                                    if args.market_retrieved_at else shared or now)
                # Parse both files before inserting rows, so malformed input imports nothing.
                articles = parse_news(args.news, news_retrieved)
                bars = parse_market(args.market)
                result = ingest(db, articles, bars, market_retrieved)
                result["news_retrieved_at"] = news_retrieved.isoformat()
                result["market_retrieved_at"] = market_retrieved.isoformat()
            elif args.command == "rank":
                as_of = timestamp(args.as_of, "as_of")
                config = json.loads(Path(args.config).read_text(encoding="utf-8"))
                result = rank(db, as_of, config, args.top_k)
                result["run_id"] = str(uuid4())
                result["run_timestamp"] = datetime.now(timezone.utc).isoformat()
                result["scoring_config"] = config
                save_run(db, result["run_id"], as_of, datetime.fromisoformat(result["run_timestamp"]), config, result)
                if args.output:
                    output = Path(args.output)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            else:
                memory = QdrantMemory(args.qdrant_url, args.collection,
                                      embedding(args.embedding_provider, args.embedding_model))
                if args.command == "memory-index":
                    result = {"indexed_articles": memory.index_articles(db), "embedding_model": memory.embedder.name}
                else:
                    convert = lambda value, field: timestamp(value, field) if value else None
                    result = memory.search_memory(args.query, args.ticker,
                        convert(args.published_before, "published_before"),
                        convert(args.published_after, "published_after"), args.limit,
                        convert(args.available_before, "available_before"))
        print(json.dumps(result, indent=2))
        return 0
    except (DataError, OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"Agent 1 error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
