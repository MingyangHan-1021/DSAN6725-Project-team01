"""Collect unlabelled news candidates from RSS or Atom feeds.

The collector intentionally records only observable source data. It does not
guess PRIMARY_TICKER or EVENT_TYPE, so its JSONL output can be used for a
human-labelled evaluation dataset without leaking automatic labels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str | None) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip()


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = [
        (key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def iso_timestamp(value: str) -> str:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(text)
    if parsed.tzinfo is None:
        raise ValueError(f"publication timestamp has no timezone: {value!r}")
    return parsed.isoformat()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, names: Iterable[str]) -> str:
    allowed = set(names)
    for child in node:
        if _local_name(child.tag) in allowed and child.text:
            return child.text.strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    for child in node:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if href and child.attrib.get("rel", "alternate") == "alternate":
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_feed(xml: bytes, source: str, retrieved_at: datetime) -> list[dict[str, str]]:
    """Parse RSS/Atom XML into unlabelled, provenance-preserving records."""
    root = ET.fromstring(xml)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    records: list[dict[str, str]] = []
    for node in entries:
        title = plain_text(_child_text(node, {"title"}))
        link = canonical_url(_entry_link(node))
        published = _child_text(node, {"pubDate", "published", "updated"})
        content = plain_text(_child_text(node, {"description", "summary", "content", "encoded"}))
        if not (title and link and published):
            continue
        try:
            published_at = iso_timestamp(published)
        except (TypeError, ValueError):
            continue
        stable_key = link or f"{source}\n{title}\n{published_at}"
        item_source = plain_text(_child_text(node, {"source"})) or source
        records.append({
            "ID": f"NEWS-{sha256(stable_key.encode('utf-8')).hexdigest()[:16].upper()}",
            "TITLE": title,
            "SOURCE": item_source,
            "URL": link,
            "PUBLISHED_AT": published_at,
            "RETRIEVED_AT": retrieved_at.isoformat(),
            "CONTENT": content or title,
        })
    return records


def collect(feeds: list[dict[str, str]], retrieved_at: datetime, timeout: float = 20.0) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for feed in feeds:
        source, url = feed["source"].strip(), feed["url"].strip()
        request = Request(url, headers={"User-Agent": "DSAN6725-eval-news-collector/1.0 student-research"})
        try:
            with urlopen(request, timeout=timeout) as response:
                xml = response.read()
            parsed = parse_feed(xml, source, retrieved_at)
        except (ET.ParseError, OSError, ValueError) as exc:
            print(f"news_collector warning: skipped {source} ({url}): {exc}", file=sys.stderr)
            continue
        for record in parsed:
            if record["URL"] not in seen_urls:
                seen_urls.add(record["URL"])
                records.append(record)
    return records


def write_jsonl(records: list[dict[str, str]], output: Path, overwrite: bool = False) -> None:
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}")
    if not records:
        raise ValueError("no usable articles were collected")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def load_feeds(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("feed config must be a nonempty JSON list")
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict) or set(item) != {"source", "url"}:
            raise ValueError(f"feed {index} must contain exactly source and url")
        if not all(isinstance(item[key], str) and item[key].strip() for key in ("source", "url")):
            raise ValueError(f"feed {index} source and url must be nonempty strings")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect unlabelled evaluation news from RSS/Atom")
    parser.add_argument("--feeds", required=True, help="JSON file containing source/url feed pairs")
    parser.add_argument("--output", required=True, help="output JSONL path")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        retrieved_at = datetime.now(timezone.utc)
        records = collect(load_feeds(Path(args.feeds)), retrieved_at, args.timeout)
        write_jsonl(records, Path(args.output), args.overwrite)
        print(json.dumps({"articles_written": len(records), "output": args.output}))
        return 0
    except (ET.ParseError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"news_collector error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
