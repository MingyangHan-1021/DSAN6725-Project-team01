"""Create reproducible model predictions for unlabelled evaluation news."""
from __future__ import annotations

import argparse
from enum import Enum
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import BaseModel


PROMPT_VERSION = "news-label-v1"
DEFAULT_MODEL = "gpt-5.4-nano-2026-03-17"
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class EventType(str, Enum):
    earnings = "earnings"
    guidance = "guidance"
    merger_acquisition = "m&a"
    rating = "rating"
    contract = "contract"
    product = "product"
    management = "management"
    regulatory = "regulatory"
    other = "other"


class ModelLabel(BaseModel):
    tickers: list[str]
    primary_ticker: str | None
    event_type: EventType


SYSTEM_PROMPT = """You label financial news for an evaluation dataset.

Return only the structured fields requested by the schema.

TICKERS:
- Include only exchange ticker symbols that are clearly supported by the supplied headline and summary.
- Use uppercase US-style symbols. Do not infer a ticker from an uncertain company-name match.
- Use an empty list when no public-company ticker is reliably identifiable.

PRIMARY_TICKER:
- Choose the one company most central to the article.
- It must appear in TICKERS.
- Use null when TICKERS is empty or no single primary ticker is supportable.

EVENT_TYPE: choose exactly one.
- earnings: reported financial results.
- guidance: forward financial or operating outlook is the main event.
- m&a: merger, acquisition, takeover, divestiture, or material stake transaction.
- rating: analyst upgrade, downgrade, initiation, or price-target action is the main event.
- contract: awarded, signed, renewed, or lost a material commercial/government contract.
- product: product/service launch, major update, trial result, or commercial availability.
- management: executive or board appointment, departure, resignation, or succession.
- regulatory: regulator, court, enforcement, approval, rule, filing, or compliance action.
- other: none of the above is the primary event.

When several types appear, label the event that is the main reason the article exists. Do not use
later knowledge or browse the web."""


def _validate_label(label: ModelLabel) -> ModelLabel:
    tickers = list(dict.fromkeys(item.strip().upper() for item in label.tickers))
    if any(not TICKER_PATTERN.fullmatch(item) for item in tickers):
        raise ValueError(f"model returned an invalid ticker list: {tickers}")
    primary = label.primary_ticker.strip().upper() if label.primary_ticker else None
    if primary is not None and primary not in tickers:
        raise ValueError("model PRIMARY_TICKER is not present in TICKERS")
    return ModelLabel(tickers=tickers, primary_ticker=primary, event_type=label.event_type)


def classify(client: Any, article: dict[str, Any], model: str) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({
                "ID": article["ID"],
                "TITLE": article["TITLE"],
                "SOURCE": article["SOURCE"],
                "PUBLISHED_AT": article["PUBLISHED_AT"],
                "CONTENT": article["CONTENT"],
            }, ensure_ascii=False)},
        ],
        text_format=ModelLabel,
    )
    latency_ms = round((time.perf_counter() - started) * 1000)
    if response.output_parsed is None:
        raise ValueError(f"model returned no parsed output for {article['ID']}")
    label = _validate_label(response.output_parsed)
    usage = getattr(response, "usage", None)
    return {
        "ID": article["ID"],
        "MODEL": model,
        "PROMPT_VERSION": PROMPT_VERSION,
        "TICKERS": label.tickers,
        "PRIMARY_TICKER": label.primary_ticker,
        "EVENT_TYPE": label.event_type.value,
        "LATENCY_MS": latency_ms,
        "INPUT_TOKENS": getattr(usage, "input_tokens", None),
        "OUTPUT_TOKENS": getattr(usage, "output_tokens", None),
        "REQUEST_ID": getattr(response, "_request_id", None),
    }


def read_articles(path: Path) -> list[dict[str, Any]]:
    articles = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = {"ID", "TITLE", "SOURCE", "PUBLISHED_AT", "CONTENT"}
    seen: set[str] = set()
    for line_number, article in enumerate(articles, 1):
        if not isinstance(article, dict) or not required <= article.keys():
            raise ValueError(f"input line {line_number} is missing required article fields")
        if article["ID"] in seen:
            raise ValueError(f"duplicate article ID: {article['ID']}")
        seen.add(article["ID"])
    return articles


def write_predictions(client: Any, articles: Iterable[dict[str, Any]], output: Path,
                      model: str, overwrite: bool = False) -> int:
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}")
    selected = list(articles)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    try:
        with partial.open("w", encoding="utf-8") as handle:
            for index, article in enumerate(selected, 1):
                prediction = classify(client, article, model)
                handle.write(json.dumps(prediction, ensure_ascii=False) + "\n")
                handle.flush()
                print(json.dumps({"completed": index, "total": len(selected), "id": article["ID"]}))
        partial.replace(output)
    except BaseException:
        raise
    return len(selected)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Label evaluation news with an OpenAI model")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not configured")
        articles = read_articles(Path(args.input))
        if args.limit is not None:
            if args.limit <= 0:
                raise ValueError("--limit must be positive")
            articles = articles[:args.limit]
        count = write_predictions(OpenAI(), articles, Path(args.output), args.model, args.overwrite)
        print(json.dumps({"predictions_written": count, "model": args.model, "output": args.output}))
        return 0
    except (FileExistsError, OSError, ValueError, json.JSONDecodeError, OpenAIError) as exc:
        print(f"auto_label_news error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
