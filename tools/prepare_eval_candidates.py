"""Merge, deduplicate, and balance unlabelled evaluation candidates."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any


POSITIVE_TERMS = (
    "earnings", "profit", "revenue", "sales", "forecast", "outlook", "guidance",
    "deal", "acquisition", "acquire", "merger", "takeover", "stake", "investment",
    "launch", "product", "contract", "approval", "ceo", "chief", "executive",
    "appoint", "depart", "resign", "layoff", "jobs", "lawsuit", "regulator",
    "fine", "sec ", "tariff", "stock", "shares", "ipo", "bond", "bank",
    "drug", "trial", "factory", "supplier", "forecast", "funding", "valuation",
)
NEGATIVE_TERMS = (
    "podcast", "wedding present", "dating", "sports piracy", "basketball",
    "football", "medicare", "social security", "career expert", "dinner party",
    "what are your rights", "scam", "state pension", "bank notes",
    "stock price, news, quote & history",
    "renting for the rest of my life",
)
TRUSTED_SOURCES = {
    "BBC", "BBC Business", "Barron's", "Benzinga", "BioPharma Dive", "Bloomberg",
    "Business Wire", "CBC", "CFO Dive", "CNA Business", "CNBC", "CoinDesk",
    "ESG Dive", "Financial Times", "Fierce Healthcare", "Fierce Pharma", "Fortune",
    "GlobeNewswire", "Investor's Business Daily", "Investing.com", "Livemint",
    "MIT Sloan", "MarketBeat", "MarketWatch", "Morningstar", "NPR", "New York Times",
    "OpenAI", "PR Newswire", "Pensions & Investments", "Politico", "Reuters",
    "Retail Dive", "SEC.gov", "STAT", "Seeking Alpha", "TechCrunch", "The Business Journals",
    "The Business Times", "The Globe and Mail", "The Guardian", "The Independent",
    "The Straits Times", "TheStreet", "TipRanks", "Trucking Dive",
    "U.S. Food and Drug Administration (.gov)", "U.S. Securities and Exchange Commission",
    "USA Today", "WSJ", "Yahoo Finance", "Yahoo! Finance Canada",
}


def publisher(source: str) -> str:
    if source.startswith("Bloomberg"):
        return "Bloomberg"
    if source.startswith("New York Times"):
        return "New York Times"
    if source.startswith("Financial Times"):
        return "Financial Times"
    return source


def relevance_score(record: dict[str, Any]) -> int:
    text = f" {record['TITLE']} ".lower()
    score = sum(2 for term in POSITIVE_TERMS if term in text)
    score -= sum(5 for term in NEGATIVE_TERMS if term in text)
    if len(record.get("CONTENT", "")) >= 80:
        score += 1
    return score


def prepare(paths: list[Path], limit: int, max_per_source: int,
            allowed_sources: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    unique: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: record must be an object")
            if "PRIMARY_TICKER" in record or "EVENT_TYPE" in record:
                raise ValueError(f"{path}:{line_number}: candidate contains an annotation field")
            url = record.get("URL")
            if not isinstance(url, str) or not url:
                raise ValueError(f"{path}:{line_number}: missing URL")
            normalized = dict(record)
            normalized["SOURCE"] = publisher(str(record["SOURCE"]))
            if allowed_sources is not None and normalized["SOURCE"] not in allowed_sources:
                continue
            if url in unique:
                duplicate_count += 1
            else:
                unique[url] = normalized

    ranked = sorted(
        unique.values(),
        key=lambda item: (
            relevance_score(item),
            datetime.fromisoformat(item["PUBLISHED_AT"].replace("Z", "+00:00")).timestamp(),
            item["ID"],
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for record in ranked:
        source = record["SOURCE"]
        if counts.get(source, 0) >= max_per_source:
            continue
        selected.append(record)
        counts[source] = counts.get(source, 0) + 1
        if len(selected) == limit:
            break
    if len(selected) < limit:
        raise ValueError(f"only {len(selected)} candidates fit the source cap; requested {limit}")
    return selected, {"raw": sum(1 for p in paths for line in p.read_text(encoding="utf-8").splitlines() if line.strip()),
                      "unique": len(unique), "duplicates_removed": duplicate_count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare balanced, unlabelled evaluation candidates")
    parser.add_argument("--input", action="append", required=True, help="input JSONL; repeat as needed")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-per-source", type=int, default=50)
    parser.add_argument("--trusted-only", action="store_true", help="use the reviewed publisher allowlist")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print(f"prepare_eval_candidates error: output already exists: {output}")
        return 2
    try:
        allowed = TRUSTED_SOURCES if args.trusted_only else None
        selected, stats = prepare([Path(item) for item in args.input], args.limit, args.max_per_source, allowed)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected), encoding="utf-8")
        print(json.dumps({**stats, "selected": len(selected), "output": str(output)}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"prepare_eval_candidates error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
