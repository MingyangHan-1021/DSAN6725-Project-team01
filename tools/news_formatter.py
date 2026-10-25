"""Convert collected or classified news JSONL into Agent 1's TXT input format.

This tool is deliberately independent of any news or LLM provider. A collector or
LLM can write JSONL, while this module validates the agreed contract and produces
a deterministic, ingestible TXT file.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from stock_agent.ingestion import parse_news
from stock_agent.models import DataError, EVENT_TYPES, timestamp, ticker_symbol

FIELDS = (
    "ID", "TITLE", "SOURCE", "URL", "PUBLISHED_AT", "TICKERS",
    "PRIMARY_TICKER", "EVENT_TYPE", "IS_HEADLINE", "CONTENT",
)
# Classification is optional for Agent 1; ticker mentions drive news scoring.
REQUIRED = frozenset(FIELDS) - {"PRIMARY_TICKER", "EVENT_TYPE", "IS_HEADLINE"}
RESERVED_LINES = frozenset({"=== ARTICLE ===", "=== END ==="})
OPTIONAL = frozenset({"RETRIEVED_AT"})


def _nonempty_string(record: dict[str, Any], field: str, line_number: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"JSONL line {line_number}: {field} must be a nonempty string")
    return value.strip()


def _tickers(record: dict[str, Any], line_number: int) -> list[str]:
    value = record.get("TICKERS")
    if isinstance(value, str):
        values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = [item.strip() for item in value if item.strip()]
    else:
        raise DataError(f"JSONL line {line_number}: TICKERS must be a string or list of strings")
    return list(dict.fromkeys(ticker_symbol(item.replace(".", "-")) for item in values))


def render_record(record: dict[str, Any], line_number: int) -> str:
    missing = sorted(REQUIRED - record.keys())
    unknown = sorted(record.keys() - set(FIELDS) - OPTIONAL)
    if missing or unknown:
        raise DataError(f"JSONL line {line_number}: missing {missing}; unknown {unknown}")

    article_id = _nonempty_string(record, "ID", line_number)
    title = _nonempty_string(record, "TITLE", line_number)
    source = _nonempty_string(record, "SOURCE", line_number)
    url = _nonempty_string(record, "URL", line_number)
    published_at = _nonempty_string(record, "PUBLISHED_AT", line_number)
    timestamp(published_at, f"JSONL line {line_number} PUBLISHED_AT")
    tickers = _tickers(record, line_number)
    # A classifier may enrich a record later, but partial classification is invalid.
    has_primary = "PRIMARY_TICKER" in record
    has_event = "EVENT_TYPE" in record
    if has_primary != has_event:
        raise DataError(f"JSONL line {line_number}: PRIMARY_TICKER and EVENT_TYPE must be supplied together")
    if has_primary:
        primary = ticker_symbol(_nonempty_string(record, "PRIMARY_TICKER", line_number).replace(".", "-"))
        if primary not in tickers:
            raise DataError(f"JSONL line {line_number}: PRIMARY_TICKER must also appear in TICKERS")
        event_type = _nonempty_string(record, "EVENT_TYPE", line_number)
        if event_type not in EVENT_TYPES:
            raise DataError(f"JSONL line {line_number}: unsupported EVENT_TYPE {event_type!r}")
    headline = record.get("IS_HEADLINE", "unknown")
    if headline not in ("true", "false", "unknown"):
        raise DataError(f"JSONL line {line_number}: invalid IS_HEADLINE {headline!r}")
    content = _nonempty_string(record, "CONTENT", line_number)
    if any(line.strip() in RESERVED_LINES for line in content.splitlines()):
        raise DataError(f"JSONL line {line_number}: CONTENT contains a reserved record marker")

    lines = [
        "=== ARTICLE ===",
        f"ID: {article_id}",
        f"TITLE: {title}",
        f"SOURCE: {source}",
        f"URL: {url}",
        f"PUBLISHED_AT: {published_at}",
        f"TICKERS: {', '.join(tickers)}",
    ]
    if has_primary:
        lines.extend((f"PRIMARY_TICKER: {primary}", f"EVENT_TYPE: {event_type}"))
    lines.extend((
        f"IS_HEADLINE: {headline}",
        "CONTENT:",
        content,
        "=== END ===",
    ))
    return "\n".join(lines)


def format_jsonl(source: str | Path, output: str | Path, retrieved_at: datetime,
                 overwrite: bool = False) -> int:
    source, output = Path(source), Path(output)
    if output.exists() and not overwrite:
        raise DataError(f"output already exists: {output}")
    rendered: list[str] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DataError(f"JSONL line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise DataError(f"JSONL line {line_number}: each record must be an object")
        rendered.append(render_record(record, line_number))
    if not rendered:
        raise DataError("news JSONL contains no records")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    try:
        partial.write_text("\n\n".join(rendered) + "\n", encoding="utf-8")
        parsed = parse_news(partial, retrieved_at)
        if len(parsed) != len(rendered):
            raise DataError("formatted news did not round-trip through parse_news")
        if output.exists():
            output.unlink()
        partial.rename(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return len(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate collected or classified news JSONL and write Agent 1 TXT")
    parser.add_argument("--input", required=True, help="news JSONL input")
    parser.add_argument("--output", required=True, help="Agent 1 news TXT output")
    parser.add_argument("--retrieved-at", required=True, help="actual retrieval time in ISO 8601 format")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        retrieved_at = timestamp(args.retrieved_at, "retrieved_at")
        count = format_jsonl(args.input, args.output, retrieved_at, args.overwrite)
        print(json.dumps({"articles_written": count, "output": args.output}))
        return 0
    except (DataError, OSError) as exc:
        print(f"news_formatter error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
