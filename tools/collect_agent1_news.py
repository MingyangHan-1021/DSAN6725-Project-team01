"""Collect and ingest all eligible feed articles for Agent 1, with optional LLM labels.

Feed retrieval supplies evidence; the model may label a primary company and event
but never supplies article counts or the final news score.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

from stock_agent.ingestion import parse_news
from stock_agent.memory import QdrantMemory, HashEmbedding
from stock_agent.models import DataError, timestamp
from stock_agent.storage import connect, ingest
from tools.filter_news_candidates import filter_records, load_company_names, read_jsonl
from tools.news_collector import collect, load_feeds
from tools.news_formatter import format_jsonl


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def label_optional(rows: list[dict], model: str, client=None) -> tuple[list[dict], list[dict]]:
    """Call the existing classifier only when explicitly requested by the caller."""
    from dotenv import load_dotenv
    from openai import OpenAI
    from tools.auto_label_news import classify, PROMPT_VERSION

    if client is None:
        load_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            raise DataError("--llm-label requires OPENAI_API_KEY")
        client = OpenAI()
    labelled, telemetry = [], []
    for row in rows:
        try:
            prediction = classify(client, row, model)
        except Exception as exc:
            raise DataError(f"LLM label failed for article {row['ID']}: {exc}") from exc
        result = dict(row)
        if prediction["PRIMARY_TICKER"]:
            result["PRIMARY_TICKER"] = prediction["PRIMARY_TICKER"]
            result["EVENT_TYPE"] = prediction["EVENT_TYPE"]
        labelled.append(result)
        telemetry.append({**prediction, "PROMPT_VERSION": PROMPT_VERSION})
    return labelled, telemetry


def run(raw_rows: list[dict], retrieved_at: datetime, output_prefix: Path,
        db_path: Path, company_map: Path, label_model: str | None = None,
        qdrant_url: str | None = None, collection: str = "agent1_news",
        browser_manifest: dict | None = None) -> dict:
    """Validate, store, and optionally index a complete eligible batch."""
    if not raw_rows:
        raise DataError("no news articles were collected")
    eligible, filter_stats = filter_records(raw_rows, load_company_names(company_map),
                                            use_supplied_tickers=browser_manifest is not None)
    if not eligible:
        raise DataError("no collected articles passed the ticker and content checks")
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    candidates_path = output_prefix.with_name(output_prefix.name + "_candidates.jsonl")
    news_path = output_prefix.with_name(output_prefix.name + "_news.txt")
    manifest_path = output_prefix.with_name(output_prefix.name + "_manifest.json")
    labelled_path = output_prefix.with_name(output_prefix.name + "_labelled.jsonl")
    telemetry_path = output_prefix.with_name(output_prefix.name + "_label_telemetry.jsonl")
    targets = [candidates_path, news_path, manifest_path]
    if label_model:
        targets.extend((labelled_path, telemetry_path))
    existing = [path for path in targets if path.exists()]
    if existing:
        raise DataError(f"output already exists: {', '.join(str(path) for path in existing)}")

    # This production path deliberately keeps every eligible article; the separate
    # evaluation candidate tool applies a limit and source balancing.
    write_jsonl(candidates_path, eligible)
    news_rows = eligible
    if label_model:
        news_rows, telemetry = label_optional(eligible, label_model)
        write_jsonl(labelled_path, news_rows)
        write_jsonl(telemetry_path, telemetry)
    source = labelled_path if label_model else candidates_path
    format_jsonl(source, news_path, retrieved_at)
    articles = parse_news(news_path, retrieved_at)
    with closing(connect(db_path)) as db, db:
        stored = ingest(db, articles, [], retrieved_at)
        indexed = (QdrantMemory(qdrant_url, collection, HashEmbedding()).index_articles(db)
                   if qdrant_url else None)
    manifest = {
        "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
        "raw_articles": len(raw_rows), "filter": filter_stats,
        "articles_stored": stored["articles_total"], "articles_in_batch": len(articles),
        "distinct_tickers": len({ticker for row in eligible for ticker in row["TICKERS"]}),
        "distinct_publishers": len({row["SOURCE"] for row in eligible}),
        "latest_published_at": max(timestamp(row["PUBLISHED_AT"], "PUBLISHED_AT")
                                   for row in eligible).isoformat(),
        "label_model": label_model, "qdrant_collection": collection if qdrant_url else None,
        "indexed_articles": indexed, "news_file": str(news_path), "database": str(db_path),
        "coverage_note": "Only articles observed in the configured feeds are counted.",
    }
    if browser_manifest is not None:
        manifest["browser_collection"] = browser_manifest
        manifest["coverage_note"] = "Browser-observed articles only; source coverage is not verified complete."
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--feeds", help="JSON feed configuration for a live collection")
    source.add_argument("--raw-input", help="saved collector JSONL for an offline rerun")
    source.add_argument("--browser-input", help="validated browser collector JSONL")
    parser.add_argument("--browser-manifest", help="audit JSON produced with --browser-input")
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--company-map", default="config/sp500_companies.csv")
    parser.add_argument("--llm-label", action="store_true", help="opt in to paid primary-ticker/event labels")
    parser.add_argument("--model", default="gpt-5.4-nano-2026-03-17")
    parser.add_argument("--qdrant-url", help="optional Qdrant evidence index URL")
    parser.add_argument("--collection", default="agent1_news")
    args = parser.parse_args(argv)
    try:
        if args.browser_input and not args.browser_manifest:
            raise DataError("--browser-input requires --browser-manifest")
        if args.browser_manifest and not args.browser_input:
            raise DataError("--browser-manifest requires --browser-input")
        browser_manifest = None
        if args.raw_input or args.browser_input:
            rows = read_jsonl([Path(args.raw_input or args.browser_input)])
            if not rows:
                raise DataError("collector JSONL has no articles to ingest")
            retrieved_at = max(timestamp(row["RETRIEVED_AT"], "RETRIEVED_AT") for row in rows)
            if args.browser_input:
                browser_manifest = json.loads(Path(args.browser_manifest).read_text(encoding="utf-8"))
                if browser_manifest.get("method") != "llm_assisted_browser" or browser_manifest.get("articles") != len(rows):
                    raise DataError("browser manifest method/article count does not match input")
        else:
            rows = collect(load_feeds(Path(args.feeds)), datetime.now(timezone.utc))
            # Use the end of collection as conservative first availability.
            retrieved_at = datetime.now(timezone.utc)
            for row in rows:
                row["RETRIEVED_AT"] = retrieved_at.isoformat()
        result = run(rows, retrieved_at, Path(args.output_prefix), Path(args.db),
                     Path(args.company_map), args.model if args.llm_label else None,
                     args.qdrant_url, args.collection, browser_manifest)
        print(json.dumps(result, indent=2))
        return 0
    except (DataError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"collect_agent1_news error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
