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
