# Pre-Market Stock Research — Agent 1 prototype

This repository's current executable scope is **Agent 1 data integration and hot-stock ranking**. It accepts teammate-produced news TXT and OHLCV CSV, stores validated records in SQLite, scores stocks from observed data, and can index article evidence in Qdrant. All included input data is synthetic. The wider four-agent goal is in [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).

## Quick start

Python 3.11+ is sufficient for ingestion, ranking, and tests. The checked-in files under `data/samples/` are synthetic:

```powershell
python -m unittest discover -s tests -v
python main.py ingest --news data/samples/news.txt --market data/samples/market_data.csv --retrieved-at 2026-10-08T08:00:00-04:00
python main.py rank --as-of 2026-10-08T09:00:00-04:00 --top-k 10 --output outputs/sample-ranking.json
```

The sample contains two synthetic tickers, so the result has two rows. For real data, replace the sample paths and provide the actual retrieval and cutoff times. Set `--db path/to/file.sqlite3` before the subcommand to use another database. The default is `data/agent1.sqlite3`. Delete or archive that file to reset local data; it is ignored by Git. The ranked JSON is saved in SQLite as an immutable run and optionally exported to `--output`.

## Input contracts

News consists of records delimited by `=== ARTICLE ===` and `=== END ===`. Each record needs `ID`, `TITLE`, `SOURCE`, `URL`, `PUBLISHED_AT`, `TICKERS`, `IS_HEADLINE`, and a multiline `CONTENT:` body. `PUBLISHED_AT` must have an ISO 8601 timezone offset; `TICKERS` may be empty; `IS_HEADLINE` is exactly `true`, `false`, or `unknown`. `unknown` does not count as verified homepage placement. Article ID is stable and cannot later identify different text, URL, publication time, or tickers.

Market CSV columns must be exactly `ticker,timestamp,timeframe,session,open,high,low,close,volume` in that order. Timestamp requires a timezone offset. OHLC must be finite, positive, and internally consistent; volume is a nonnegative integer. `(ticker,timestamp,timeframe,session)` is unique. The baseline ranking uses only `1Day` / `regular` bars. A malformed teammate file fails with a record or line error; no part of that ingest is committed.

Pass the **actual first retrieval time** with `--retrieved-at` when available. If omitted, ingestion uses the current UTC time. This first observation becomes `available_at` for both articles and market bars and is never moved backwards by a later repeat ingest. All stored timestamps are UTC. Historical `--as-of` queries require both event time and availability time to be at or before the cutoff, so a file imported today cannot be treated as known during an older backtest. The fixture command pins a synthetic retrieval time strictly for demonstration; do not assign fictional earlier retrieval times to real data.

## Scoring assumptions

The active news window is the inclusive interval `[as_of - 7 calendar days, as_of]`; older articles remain in SQLite. Repeated URL or identical title/body text counts once per ticker. Source breadth counts all distinct observed publishers, including duplicate coverage. Different articles are **not** assumed to be distinct events; `unique_events` is currently unknown. No Qdrant similarity count enters news scoring.

Market features use the latest eligible daily regular-session bar and matched earlier bars: absolute one-session close change, absolute close change versus the last available session **at or before seven calendar days before the latest bar**, relative volume versus the previous three bars, and current high-low range relative to the previous three ranges. Insufficient history yields `null` and a missing-feature label. A ticker qualifies only with a matching, sufficiently recent bar and the configured minimum price and volume. The baseline settings in [config/scoring.json](config/scoring.json) use 60% market and 40% news weighting; these are exploratory weights. Each feature is normalized by rank within the eligible observed universe. Available market features are averaged; absent news in the supplied file contributes zero observed coverage. Ties resolve alphabetically by ticker.

The output is **Top 10 within supplied data**, not a verified US-wide ranking. Small or selective teammate files create coverage bias. The output includes the run ID, cutoff, window, component scores, signals, evidence IDs, and scoring configuration.

## Optional Qdrant memory

Start Qdrant locally, then index and search:

```powershell
docker run --rm -p 6333:6333 qdrant/qdrant
python main.py memory-index --qdrant-url http://localhost:6333
python main.py memory-search --query "Alder product" --ticker ALDR --published-before 2026-10-08T09:00:00-04:00
```

The default embedding is a deterministic 128-dimensional lexical hash for offline tests and plumbing. It is **not a semantic model**. For semantic embeddings, install `sentence-transformers` and use `--embedding-provider sentence-transformers --embedding-model all-MiniLM-L6-v2` for both indexing and searching. Use a separate Qdrant collection when changing the embedding model. The memory API is `QdrantMemory.search_memory(query, tickers=None, published_before=None, published_after=None, limit=10, available_before=None)`. It always filters by availability, even if the caller only supplies a publication cutoff. Qdrant is a retrieval copy; SQLite remains the structured source for counts and rankings. Ranking works when Qdrant is stopped. The code reports Qdrant connection failures rather than treating indexing as successful.

## Team integration

Replace the sample paths with the collector teammates' output files. Keep their supplied schema and provenance; address validation errors at the collection boundary. Agent 2 can consume ranking JSON and query shared memory before requesting new evidence. Agents 2–4, API collection, scheduling, and UI are outside this first version. `CODEX_TASK.md` is a local task file and is ignored by Git.
