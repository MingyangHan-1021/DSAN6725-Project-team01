# AGENTS.md — Pre-Market Stock Research and Prediction System

## Project mission
Build a multi-agent system that discovers recently active US-listed stocks, researches evidence about them, makes **frozen pre-market UP/DOWN hypotheses**, and evaluates direction and reasoning after the trading session. The product is a research and evaluation prototype, **not investment advice or an automated trading system**.

## Global architecture
1. **Hot Stock Discovery Agent:** obtains market and news signals from upstream collectors; uses deterministic features/scoring and optional LLM news classification to rank **Top 10** stocks.
2. **Research & Reasoning Agent:** combines Top 10 with user-added watchlist tickers, searches targeted company/sector/macro sources, and outputs cited bullish/bearish evidence, causal hypotheses, and uncertainties.
3. **Daily Direction Prediction Agent:** uses only the frozen, pre-cutoff research package and pre-cutoff market data; emits UP/DOWN, confidence, reasons, counterargument, and timestamp. No new web searches. The precise price target (e.g., regular-session close vs open) must be configured consistently.
4. **Evaluation Agent:** after market close, uses observed OHLCV for deterministic direction correctness, and post-market sourced evidence for cautious LLM reasoning assessment (supported/partially supported/unsupported/inconclusive). Commentary is evidence, not definitive causal ground truth.

## Current implementation ownership
Current developer owns **Agent 1 integration infrastructure**, not upstream API collection. Teammates provide structured `news.txt` and `market_data.csv`. Implement ingestion, validation, storage, Qdrant shared memory, market/news features, ranking and Top 10 output. Keep Alpaca/web-search clients and Agents 2–4 as future interfaces/stubs, not invented implementations. Google Trends is **out of scope**.

## Shared research memory policy
- Store structured news metadata in SQLite and semantic vectors/payloads in Qdrant. Do not use vector similarity counts as news-frequency measurements.
- **Memory first, web second** for future research agents: retrieve existing relevant, fresh, source-attributed evidence before requesting more searches; new results should be written back with stable IDs and provenance.
- Keep `published_at`, `retrieved_at`, and when possible `available_at`, with timezone information. Enforce cutoff on information availability, not just event date. Information discovered after a historical cutoff must not be backfilled into an earlier prediction.
- Use a rolling **7 calendar-day event/news window** for current research and scoring. Keep historical records for evaluation; expire from current windows without deleting history.
- Deduplicate articles and distinguish repeated coverage from distinct events. Homepage headline status must be verified; a normal article title is not evidence of homepage placement.

## Data boundaries and contracts
- News TXT: `=== ARTICLE ===` / `=== END ===` records with ID, title, source, URL, publication time, tickers, headline status, and content.
- Market CSV: `ticker,timestamp,timeframe,session,open,high,low,close,volume` with timezone-aware timestamps.
- Prefer schema-validated structured objects between modules. Do not silently coerce malformed records, mix regular/premarket bars, invent missing data, or assert full-market coverage when the input dataset is partial.
- SQLite is the canonical source for OHLCV, article metadata, features, scores, predictions and evaluation; Qdrant is a semantic evidence retrieval layer. Keep the embedding model/provider configurable and testable without network access.

## Agent outputs
- Agent 1: ranked Top 10 with ticker, market score, news score, hotness score, main signals, evidence IDs, window and run timestamp.
- Agent 2: per-ticker structured research with bullish/bearish evidence, sources, macro/sector exposures and uncertainty.
- Agent 3: immutable timestamped predictions with confidence and supporting/counter evidence.
- Agent 4: deterministic direction result and separately labeled, evidence-based reasoning assessment.

## Engineering principles
- Prefer deterministic code for parsing, validation, quantitative calculations, score aggregation and direction accuracy; use LLMs only where semantic understanding or causal reasoning adds value.
- Modular Python with typed functions, documented interfaces, configuration files, environment variables for secrets, stable IDs, idempotent upserts, clear logs, retries where appropriate, and focused tests.
- Make scoring weights configurable (initial exploratory baseline: 60% market, 40% news). Normalize signals before combination. Explicitly handle insufficient history, source coverage, and missing features.
- Never hardcode credentials, commit `.env` secrets, or place trades. Avoid unnecessary paid model calls and duplicate searches; track token counts, latency and cost where applicable.
- Preserve reproducibility: record model versions, prompt versions, source timestamps, feature/scoring versions, and run IDs. Evaluation must avoid look-ahead bias and survivorship bias where feasible.

## Codex working rules
1. Inspect repository and existing code before modifying it. Respect teammates' files and current conventions.
2. Implement the smallest complete vertical slice before introducing heavy orchestration frameworks or a web UI.
3. Do not claim tests, APIs, Qdrant, or cloud deployment succeeded without actually running them.
4. Provide synthetic fixtures, setup instructions, unit/integration tests, and meaningful error messages.
5. When requirements are ambiguous, document assumptions; do not fabricate market/news evidence.
6. Keep current scope distinct from the full four-agent roadmap. Do not implement future agents unless explicitly requested.

## Git rules
- Do not commit or push CODEX_TASK.md. It is for local task.