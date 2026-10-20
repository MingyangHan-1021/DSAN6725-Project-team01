"""Attach S&P 500 tickers and remove unusable news evaluation candidates."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable

try:
    from tools.prepare_eval_candidates import prepare
except ModuleNotFoundError:  # Support ``python tools/filter_news_candidates.py``.
    from prepare_eval_candidates import prepare


CORPORATE_SUFFIXES = {
    "co", "company", "companies", "corp", "corporation", "inc", "incorporated",
    "limited", "ltd", "nv", "plc", "sa",
}
GENERIC_NAMES = {
    "all", "ball", "best buy", "block", "booking", "carrier", "charter communications", "cintas",
    "citizens", "consolidated edison", "copart", "dayforce", "eversource energy",
    "expeditors international", "fidelity national information services", "first solar",
    "fortive", "fortune brands innovations", "international paper", "iron mountain",
    "live nation entertainment", "news corp", "northern trust", "principal financial",
    "coherent", "cooper", "flex", "mosaic", "nasdaq", "news", "public storage",
    "republic services", "southern", "state street", "target",
    "tractor supply", "travelers", "united rentals", "western digital",
    "waters", "westinghouse air brake technologies", "williams",
}
CANONICAL_SHARE_CLASS = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA"}

# Common editorial names that differ from the formal Security names in the frozen table.
# Every value must also exist in config/sp500_tickers.txt.
ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("Apple",),
    "AMZN": ("Amazon",),
    "BRK.B": ("Berkshire", "Berkshire Hathaway"),
    "DELL": ("Dell",),
    "DIS": ("Disney",),
    "GOOGL": ("Alphabet", "Google"),
    "JPM": ("JPMorgan", "JPMorgan Chase"),
    "META": ("Meta", "Facebook"),
    "MS": ("Morgan Stanley",),
    "MSFT": ("Microsoft",),
    "NFLX": ("Netflix",),
    "NTAP": ("NetApp",),
    "NVDA": ("Nvidia",),
    "ORCL": ("Oracle",),
    "SBUX": ("Starbucks",),
    "T": ("AT&T",),
    "TSLA": ("Tesla",),
    "WMT": ("Walmart",),
}


def normalize(text: str) -> str:
    """Normalize punctuation and whitespace for conservative phrase matching."""
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold().replace("&", " and ")))


def company_variants(company_name: str) -> set[str]:
    """Return formal-name variants without inventing short, ambiguous nicknames."""
    base = re.sub(r"\([^)]*\)", " ", company_name)
    words = normalize(base).split()
    variants = {" ".join(words)}
    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    if words:
        variants.add(" ".join(words))
    return {item for item in variants if len(item) >= 4 and item not in GENERIC_NAMES}


def load_company_names(path: Path) -> dict[str, set[str]]:
    """Load the frozen ticker/name mapping and add reviewed editorial aliases."""
    names: dict[str, set[str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = row["ticker"].strip().upper()
            canonical = CANONICAL_SHARE_CLASS.get(ticker, ticker)
            names.setdefault(canonical, set()).update(company_variants(row["company_name"]))
    for ticker, aliases in ALIASES.items():
        if ticker not in names:
            raise ValueError(f"alias ticker is absent from company map: {ticker}")
        names[ticker].update(normalize(alias) for alias in aliases)
    return names


def find_tickers(record: dict[str, Any], company_names: dict[str, set[str]]) -> list[str]:
    """Find S&P 500 companies explicitly named in an article."""
    text = f" {normalize(str(record.get('TITLE', '')) + ' ' + str(record.get('CONTENT', '')))} "
    found = {
        ticker
        for ticker, names in company_names.items()
        if any(f" {name} " in text for name in names)
    }
    return sorted(found)


def content_repeats_title(title: str, content: str) -> bool:
    """Detect RSS descriptions that only repeat the headline and publisher."""
    title_words = normalize(title).split()
    content_words = normalize(content).split()
    if not content_words:
        return True
    if content_words == title_words:
        return True
    title_set, content_set = set(title_words), set(content_words)
    overlap = len(title_set & content_set) / max(1, len(title_set | content_set))
    if len(content_words) <= len(title_words) + 6 and overlap >= 0.85:
        return True
    if len(content_words) <= len(title_words) + 4:
        joined_title, joined_content = " ".join(title_words), " ".join(content_words)
        if joined_title in joined_content:
            return True
    return False


def filter_records(records: Iterable[dict[str, Any]], company_names: dict[str, set[str]]) \
        -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Attach tickers and report why records were excluded."""
    kept: list[dict[str, Any]] = []
    stats = {"input": 0, "no_sp500_ticker": 0, "title_only_content": 0, "eligible": 0}
    for record in records:
        stats["input"] += 1
        if content_repeats_title(str(record.get("TITLE", "")), str(record.get("CONTENT", ""))):
            stats["title_only_content"] += 1
            continue
        tickers = find_tickers(record, company_names)
        if not tickers:
            stats["no_sp500_ticker"] += 1
            continue
        cleaned = dict(record)
        cleaned["TICKERS"] = tickers
        kept.append(cleaned)
    stats["eligible"] = len(kept)
    return kept, stats


def read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            url = record.get("URL")
            if not isinstance(url, str) or not url:
                raise ValueError(f"{path}:{line_number}: missing URL")
            if url not in seen_urls:
                records.append(record)
                seen_urls.add(url)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--company-map", default="config/sp500_companies.csv")
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-per-source", type=int, default=50)
    parser.add_argument("--published-after", help="inclusive ISO date/timestamp lower bound")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print(f"filter_news_candidates error: output already exists: {output}")
        return 2
    try:
        paths = [Path(item) for item in args.input]
        unique = read_jsonl(paths)
        date_removed = 0
        if args.published_after:
            cutoff = datetime.fromisoformat(args.published_after.replace("Z", "+00:00"))
            dated = []
            for row in unique:
                published = datetime.fromisoformat(str(row["PUBLISHED_AT"]).replace("Z", "+00:00"))
                if published >= cutoff:
                    dated.append(row)
                else:
                    date_removed += 1
            unique = dated
        eligible, filter_stats = filter_records(unique, load_company_names(Path(args.company_map)))
        temporary = output.with_suffix(output.suffix + ".eligible")
        temporary.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in eligible), encoding="utf-8"
        )
        try:
            selected, selection_stats = prepare([temporary], args.limit, args.max_per_source)
        finally:
            temporary.unlink(missing_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8"
        )
        manifest = {
            "collection_type": "unlabelled_evaluation_candidates_with_ticker_options",
            "inputs": [str(path) for path in paths],
            **filter_stats,
            "before_date_filter": len(unique) + date_removed,
            "outside_date_window": date_removed,
            "selected": len(selected),
            "unique_urls": len(unique),
            "selection": selection_stats,
            "company_map": str(args.company_map),
            "company_map_source": "Wikipedia revision 1379247819 (2026-10-08T21:08:16Z)",
            "output": str(output),
            "quality_checks": {
                "unique_ids": len({row["ID"] for row in selected}),
                "unique_urls": len({row["URL"] for row in selected}),
                "records_with_tickers": sum(bool(row["TICKERS"]) for row in selected),
                "records_with_primary_ticker": sum("PRIMARY_TICKER" in row for row in selected),
                "records_with_event_type": sum("EVENT_TYPE" in row for row in selected),
                "title_only_content": sum(content_repeats_title(row["TITLE"], row["CONTENT"]) for row in selected),
            },
            "coverage": {
                "distinct_sources": len({row["SOURCE"] for row in selected}),
                "distinct_publication_dates": len({row["PUBLISHED_AT"][:10] for row in selected}),
                "distinct_tickers": len({ticker for row in selected for ticker in row["TICKERS"]}),
                "first_published_at": min(row["PUBLISHED_AT"] for row in selected),
                "last_published_at": max(row["PUBLISHED_AT"] for row in selected),
            },
            "known_limitations": [
                "Company-name matching is deterministic and conservative but should still be checked during human annotation.",
                "Exact URL duplicates are removed; semantic same-event clustering is not performed.",
                "TICKERS are candidate companies, not gold PRIMARY_TICKER labels.",
            ],
        }
        if args.manifest:
            Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"filter_news_candidates error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
