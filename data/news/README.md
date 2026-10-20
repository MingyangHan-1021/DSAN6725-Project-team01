# News data

This directory stores dated, real-world news inputs for Agent 1. Each collection has two files with the same date prefix:

- `<date>_news.txt`: articles in the strict `=== ARTICLE ===` / `=== END ===` ingestion format.
- `<date>_manifest.json`: retrieval window, sources, coverage, selection method, and known limitations.

Use the actual retrieval time from the manifest when ingesting a collection:

```bash
python main.py ingest \
  --news data/news/<date>_news.txt \
  --market data/model/<date>_past_7_days.csv \
  --retrieved-at <manifest-retrieved_at>
```

The files contain original factual summaries and source links, not copied article text. `IS_HEADLINE` must remain `unknown` unless homepage placement was independently verified. Do not assign an article to a ticker when the company-symbol mapping is uncertain.

Collections are focused research inputs, not claims of complete market-wide news coverage. Preserve older dated files for reproducibility and historical evaluation instead of overwriting them.

## Unlabelled evaluation candidates

`tools/news_collector.py` collects RSS/Atom metadata into JSONL without assigning
`PRIMARY_TICKER` or `EVENT_TYPE`. Copy the example feed configuration, replace it
with verified publisher feed URLs, and keep each dated output immutable:

```bash
cp config/news_feeds.example.json config/news_feeds.local.json
python -m tools.news_collector \
  --feeds config/news_feeds.local.json \
  --output data/news/candidates/<date>_unlabelled.jsonl
```

The output preserves title, publisher, canonical URL, publication time, retrieval
time, and feed summary. The collector removes common tracking parameters and
duplicate URLs within a run. It does not bypass paywalls or copy full articles.
Feed URLs must be reviewed before each collection because publisher endpoints and
terms can change. Local feed configuration is not an annotation file.

## Classified news fields

Newly classified records may add `PRIMARY_TICKER` and `EVENT_TYPE`. They must be supplied together. The primary ticker must also appear in `TICKERS`, and the event type must be one of `earnings`, `guidance`, `m&a`, `rating`, `contract`, `product`, `management`, `regulatory`, or `other`. Legacy files without these two fields remain valid.

Provider or LLM output should first be saved as JSONL, then validated and converted without any API key:

```bash
python -m tools.news_formatter \
  --input path/to/classified-news.jsonl \
  --output data/news/<date>_news.txt \
  --retrieved-at <actual-retrieval-time>
```

Each JSONL object uses the same uppercase field names as the TXT contract. `TICKERS` can be either a comma-separated string or a JSON list. The formatter refuses unknown fields, invalid timestamps or tickers, unsupported event types, and accidental overwrites.
