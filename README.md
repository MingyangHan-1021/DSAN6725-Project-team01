# Pre-Market Stock Research — Agent 1 prototype

This repository's current executable scope is **Agent 1 data integration and hot-stock ranking**. It accepts teammate-produced news TXT and OHLCV CSV, stores validated records in SQLite, scores stocks from observed data, and can index article evidence in Qdrant. `data/samples/` is synthetic; dated data under `data/news/`, `data/model/`, and `data/research/` has separate provenance in its manifests. The wider four-agent goal is in [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).

## Quick start

Python 3.11+ is sufficient for ingestion, ranking, and tests. On Windows, install the timezone database so news days follow New York daylight-saving transitions: `python -m pip install -r requirements.txt`. Browser collection additionally needs Chromium (`python -m playwright install chromium`) and an `OPENAI_API_KEY`; it makes paid model calls. The checked-in files under `data/samples/` are synthetic:

```powershell
python -m unittest discover -s tests -v
python main.py ingest --news data/samples/news.txt --market data/samples/market_data.csv --retrieved-at 2026-10-08T08:00:00-04:00
python main.py rank --as-of 2026-10-08T09:00:00-04:00 --top-k 10 --output outputs/sample-ranking.json
```

The sample contains two synthetic tickers, so the result has two rows. For real data, replace the sample paths and provide the actual retrieval and cutoff times. Set `--db path/to/file.sqlite3` before the subcommand to use another database. The default is `data/agent1.sqlite3`. Delete or archive that file to reset local data; it is ignored by Git. The ranked JSON is saved in SQLite as an immutable run and optionally exported to `--output`.

## Input contracts

News consists of records delimited by `=== ARTICLE ===` and `=== END ===`. Each record needs `ID`, `TITLE`, `SOURCE`, `URL`, `PUBLISHED_AT`, `TICKERS`, `IS_HEADLINE`, and a multiline `CONTENT:` body. Newly classified records may also include the paired fields `PRIMARY_TICKER` and `EVENT_TYPE`; legacy records remain valid without them. `PUBLISHED_AT` must have an ISO 8601 timezone offset; `TICKERS` may be empty for legacy records; `IS_HEADLINE` is exactly `true`, `false`, or `unknown`. `unknown` does not count as verified homepage placement. Article ID is stable and cannot later identify different text, URL, publication time, tickers, or classification.

Market CSV columns must be exactly `ticker,timestamp,timeframe,session,open,high,low,close,volume` in that order. Timestamp requires a timezone offset. OHLC must be finite, positive, and internally consistent; volume is a nonnegative integer. `(ticker,timestamp,timeframe,session)` is unique. The baseline ranking uses only `1Day` / `regular` bars. A malformed teammate file fails with a record or line error; no part of that ingest is committed.

Pass the **actual first retrieval time** for each source with `--news-retrieved-at` and `--market-retrieved-at`, using the corresponding manifests. The older `--retrieved-at` remains a fallback when both files genuinely share one retrieval time; if a time is omitted, that source uses the current UTC time. First observation becomes `available_at` and is never moved backwards by a later repeat ingest. All stored timestamps are UTC. Historical `--as-of` queries require both event time and availability time to be at or before the cutoff, so a file imported today cannot be treated as known during an older backtest. For a corrected historical import of previously stored records, use a new database instead of backdating the existing one. The fixture command pins a synthetic retrieval time strictly for demonstration; do not assign fictional earlier retrieval times to real data.

## Scoring assumptions

The active news window contains **seven New York calendar dates**, ending on the date of `as_of`. The final date may be partial when a run happens before midnight. Every ticker in `config/sp500_tickers.txt` gets a row on every date, even with no observed article. On each date, article mentions, distinct sources, and verified homepage headlines are separately normalized across the configured universe using `log1p(value) / log1p(daily maximum)`; a zero maximum gives zero. The configured feature weights combine them into a daily score, and the seven daily scores are averaged into the rolling news score. A ticker with no observed news scores exactly zero. Article publication assigns its news date, but a run only uses articles actually available by its `as_of`; a later discovery never changes an older saved run. The scores are stored in SQLite tables `daily_news_scores` and `rolling_news_scores`, and in the immutable ranking JSON. Older articles remain in SQLite.

Each supplied article ID tagged to a ticker counts as a mention, even when one source repeats coverage. A source counts once per ticker **per day**. Only `IS_HEADLINE: true` counts as a verified homepage headline. Repeated URL or identical title/body text is still reported separately as `unique_articles`, but that diagnostic does not reduce the mention count. Different articles are **not** assumed to be distinct events; `unique_events` is currently unknown. No Qdrant similarity count enters news scoring. Zero means no qualifying article was observed in the configured sources, not proof that no news existed.

Market features use the latest eligible daily regular-session bar and matched earlier bars: absolute one-session close change, absolute close change versus the last available session **at or before seven calendar days before the latest bar**, relative volume versus the previous three bars, and current high-low range relative to the previous three ranges. Insufficient history yields `null` and a missing-feature label. A ticker qualifies only with a matching, sufficiently recent bar and the configured minimum price and volume. The baseline settings in [config/scoring.json](config/scoring.json) use 60% market and 40% news weighting; these are exploratory weights. Market features are normalized by rank within the eligible observed universe. Available market features are averaged; absent observed news contributes zero. Ties resolve alphabetically by ticker.

The output is **Top 10 within supplied data**, not a verified US-wide ranking. Small or selective teammate files create coverage bias. The output includes the run ID, cutoff, window, component scores, signals, evidence IDs, and scoring configuration.

## Browser-led news collection (optional)

`config/browser_news_sources.json` lists Yahoo Finance, CNBC, 24/7 Wall St., PR Newswire, and GlobeNewswire. `tools/browser_news_collector.py` opens each listing in Chromium, asks the model to choose links visible there, opens those articles, and asks the model for sourced timestamps, short factual summaries, and relevant tickers. Code checks the chosen URLs against each source's allowlist, verifies that publication-time evidence appears on the opened page, checks tickers against the company map, and writes Agent 1 JSONL plus a per-source audit manifest. For Yahoo, a separately credited publisher is retained when its attribution is visible; otherwise the source remains Yahoo Finance. Article IDs use canonical URLs; homepage headline status stays `unknown`. The browser collector does not calculate counts or scores.

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
$env:OPENAI_API_KEY = "<your key>"
python -m tools.browser_news_collector --cutoff 2026-10-22T00:00:00-04:00 --db data/agent1.sqlite3 --output data/news/browser_2026-10-23.jsonl --manifest data/news/browser_2026-10-23_manifest.json
python -m tools.collect_agent1_news --browser-input data/news/browser_2026-10-23.jsonl --browser-manifest data/news/browser_2026-10-23_manifest.json --output-prefix data/news/browser_2026-10-23_batch --db data/agent1.sqlite3
```

Use an actual timezone-aware cutoff for the collection window and an actual pre-market `--as-of` when ranking. The source config caps pages and articles to bound model calls; hitting those caps marks coverage incomplete. The manifest lists visited pages, opened and already-stored articles, failures (including 429 responses), stop reasons, model version, prompt version, and token usage. `cutoff_reached` means an older article was observed or already stored, **not** proof that every intervening article was found; `coverage_verified` remains false. A source blocked by the site is reported as incomplete. Keep earlier batch JSONL and manifests to build a seven-day history; do not backdate their retrieval times. The collector reads `--db` without changing it and skips its known URLs, preserving the first observed version of an article; pass the same DB to ingestion. If a run finds zero eligible articles, keep its browser manifest but do not ingest an empty news batch.

## Optional Qdrant memory

Start Qdrant locally, then index and search:

```powershell
docker run --rm -p 127.0.0.1:6333:6333 qdrant/qdrant
python main.py memory-index --qdrant-url http://localhost:6333
python main.py memory-search --query "Alder product" --ticker ALDR --published-before 2026-10-08T09:00:00-04:00
```

On Windows without Docker, download the official `qdrant-x86_64-pc-windows-msvc.zip` release from [Qdrant releases](https://github.com/qdrant/qdrant/releases), extract `qdrant.exe` to `.local/qdrant/bin/`, then run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_qdrant.ps1`. The script starts a hidden local server at `http://127.0.0.1:6333` and keeps its persistent storage and logs under `.local/qdrant/run/`. The `.local/` directory is ignored by Git. Check `http://127.0.0.1:6333/collections` for service health; run the same script after a reboot to restart it. The Agent 1 `memory-index` command uses articles from the selected SQLite database (`--db` before the subcommand), so ingest the intended news file there first.

The default embedding is a deterministic 128-dimensional lexical hash for offline tests and plumbing. It is **not a semantic model**. For semantic embeddings, install `sentence-transformers` and use `--embedding-provider sentence-transformers --embedding-model all-MiniLM-L6-v2` for both indexing and searching. Use a separate Qdrant collection when changing the embedding model. The memory API is `QdrantMemory.search_memory(query, tickers=None, published_before=None, published_after=None, limit=10, available_before=None)`. It always filters by availability, even if the caller only supplies a publication cutoff. Qdrant is a retrieval copy; SQLite remains the structured source for counts and rankings. Ranking works when Qdrant is stopped. The code reports Qdrant connection failures rather than treating indexing as successful.

## Team integration

Replace the sample paths with the collector teammates' output files. Keep their supplied schema and provenance; address validation errors at the collection boundary. Agent 2 can consume ranking JSON and query shared memory before requesting new evidence. Agents 2–4, API collection, scheduling, and UI are outside this first version. `CODEX_TASK.md` is a local task file and is ignored by Git.
