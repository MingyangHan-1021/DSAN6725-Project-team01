# News data

This directory stores dated, real-world news inputs for Agent 1. Each collection has two files with the same date prefix:

- `<date>_news.txt`: articles in the strict `=== ARTICLE ===` / `=== END ===` ingestion format.
- `<date>_manifest.json`: retrieval window, sources, coverage, selection method, and known limitations.

Use the actual retrieval time from the manifest when ingesting a collection:

```bash
python main.py ingest \
  --news data/news/<date>_news.txt \
  --news-retrieved-at <news_manifest_retrieved_at> \
  --market data/model/<date>_past_7_days.csv \
  --market-retrieved-at <market_manifest_retrieved_at>
```

The files contain original factual summaries and source links, not copied article text. `IS_HEADLINE` must remain `unknown` unless homepage placement was independently verified. Do not assign an article to a ticker when the company-symbol mapping is uncertain.

Collections are focused research inputs, not claims of complete market-wide news coverage. Preserve older dated files for reproducibility and historical evaluation instead of overwriting them.

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
