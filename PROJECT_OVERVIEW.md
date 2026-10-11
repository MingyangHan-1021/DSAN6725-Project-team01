# Pre-Market Stock Intelligence & Prediction System

## 1. Project Overview

This project builds a multi-agent AI system that discovers stocks attracting unusual market and media attention, researches the factors influencing them, makes a **pre-market directional prediction**, and evaluates that prediction after the regular trading session.

The goal is **research and measurable evaluation**, not automated trading, investment advice, or precise price-target forecasting.

The system focuses on:
- **Top 10 hot stocks**, ranked daily using market activity and financial-news attention.
- **User watchlist**, manually maintained by the user.
- The union of these groups, with duplicate tickers removed, for downstream research and prediction.

The project should work as a scheduled daily pipeline and be deployable on a small CPU-based server such as AWS EC2. Development may happen locally.

## 2. End-to-End Workflow

```text
US Stock Universe
   |                     Financial News
   |                           |
Alpaca Market Data             |
   |                           |
   +----------+----------------+
              |
     Agent 1: Hot Stock Discovery
     - Quantitative market features
     - Media attention and event classification
     - Rolling 7-day window
     - Weighted Hotness Score
              |
         Ranked Top 10
              |
       + User Watchlist
              |
     Agent 2: Research & Reasoning
     - Targeted company, industry and macro research
     - Official sources and reputable financial media
     - Bullish/bearish evidence and causal reasoning
              |
       Structured Research Reports
              |
     Agent 3: Daily Direction Prediction
     - Uses only information available before cutoff
     - No new web searches
     - UP/DOWN + confidence + rationale
              |
       Immutable Prediction Record
              |
         Market Session Ends
              |
     Agent 4: Evaluation
     - Deterministic directional accuracy
     - Post-market evidence-based reasoning review
              |
        Evaluation Results & History
```

A **Shared Research Memory** is accessible across the pipeline, with strict time-based access controls.

## 3. The Four Agents

### Agent 1 — Hot Stock Discovery

**Purpose:** Identify the ten stocks with the strongest combination of unusual trading activity and media attention.

**Inputs:**
- US-listed stock universe.
- Market data from Alpaca (OHLCV, price moves, volume, relative volume, volatility, and available pre-market data).
- Financial news from established media sources.
- Recent events within a rolling **seven-calendar-day** window.

**Processing:**
- Calculate a **Market Score** using normalized quantitative indicators.
- Calculate a **Media Attention Score** from relevant coverage, primary-subject mentions, unique events, and coverage breadth.
- Deduplicate syndicated articles while retaining evidence of how widely an event was covered.
- Combine the two scores using configurable weights to produce a **Hotness Score**.
- Select the **Top 10**.

**LLM role:** Company/ticker extraction, article relevance, event classification, and semantic deduplication support. Numerical ranking must be deterministic.

**Output:** Ranked Top 10 with ticker, component scores, combined score, main signals, and linked evidence.

**Important:** Data acquisition is performed by other teammates. The current assigned integration module receives **news TXT** and **market CSV** files, validates and stores them, builds the shared memory, and calculates scores. Do not assume the ingestion module itself must call Alpaca or web-search APIs.

### Agent 2 — Research & Reasoning

**Purpose:** Explain why each stock in the Top 10 + user watchlist deserves attention.

**Inputs:** Selected tickers, market features, Agent 1 evidence, relevant company/sector/macro information.

**Tools/data sources:** Shared memory first; then targeted web search as needed. Prefer SEC, company investor-relations releases, Federal Reserve, BLS, BEA, Treasury, and reputable financial media. Market data comes from the existing Alpaca integration.

**LLM role:** Connect events to business mechanisms, company exposure, likely bullish and bearish implications, observed market signals, and uncertainty. This is **causal hypothesis formation**, not merely article summarization.

**Output:** Structured per-stock research report containing bullish factors, bearish factors, key events, macro/sector exposure, evidence links, and uncertainties.

### Agent 3 — Daily Direction Prediction

**Purpose:** Produce a testable same-day directional hypothesis for each selected stock.

**Inputs:** Agent 2 reports and pre-cutoff historical, overnight, and pre-market market data.

**Tools:** No new web searches. Consume only the already collected, time-eligible research and market snapshot.

**Output:** Ticker, **UP/DOWN** prediction, confidence, primary reasons, counterargument, and prediction timestamp.

**Evaluation target:** Define precisely before experimentation; the proposed baseline is **regular-session close versus regular-session open**. Specify tie/unchanged handling explicitly.

**Critical:** Freeze the prediction and its input snapshot before the predefined cutoff (illustratively 9:15 AM Eastern, using only data actually available by then). Never rewrite predictions after the session.

### Agent 4 — Evaluation

**Purpose:** Evaluate both the direction and the supporting reasoning.

**Inputs:** Frozen predictions, regular-session market data, and post-market reports.

**Tools:** Alpaca for actual open/close and other market measurements; web search for official releases and credible post-market reporting.

**Evaluation:**
- **Direction:** Deterministic code compares predicted direction to actual direction.
- **Reasoning:** LLM judges whether the original reasons are *supported*, *partially supported*, *unsupported*, or *inconclusive* given the available evidence.
- Record alternative explanations and evidence citations. Commentary is supporting evidence, not definitive causal ground truth.

**Output:** Per-stock evaluation records and aggregate metrics such as directional accuracy, confidence calibration, latency, token usage, and cost per task.

## 4. Shared Research Memory / RAG

All agents reuse a common evidence store to reduce duplicate searches and token costs.

**Memory-first rule:** Before a new web search, check existing evidence for relevance, freshness, and sufficient coverage. If information is missing or stale, perform a targeted search and store the resulting evidence.

Suggested storage split:
- **Qdrant:** News/event embeddings, short summaries, semantic retrieval, and searchable metadata.
- **SQLite locally / optionally PostgreSQL on deployment:** Structured market bars, article metadata for counts, scores, rankings, watchlists, predictions, and evaluation records.

Store source URL, ticker(s), publication timestamp, retrieval timestamp, event date, event identity, and evidence references. Keep enough history for backtesting, but filter the active hotness computation to the latest seven calendar days.

**Time integrity:** Agent 3 must never access any information unavailable at its prediction cutoff, even if Agent 4 later adds it to the shared memory. Historical backtests must apply the same as-of-time filtering. Avoid look-ahead bias.

## 5. Major Interfaces

The system should exchange **structured, validated records** between stages, not only free-form prose.

- **News ingestion:** Structured articles parsed from teammate-provided TXT; format agreed in advance.
- **Market ingestion:** Time-stamped OHLCV bars from teammate-provided CSV, including timeframe/session semantics.
- **Agent 1 → 2:** Top 10 ranked tickers, scores, market signals, evidence IDs.
- **Agent 2 → 3:** Per-ticker research report with source-backed bullish/bearish reasoning.
- **Agent 3 → 4:** Immutable prediction and its exact input/cutoff metadata.
- **Agent 4 → reporting:** Direction metrics, reasoning assessments, supporting sources, and run statistics.

All ingestion should be repeatable/idempotent, validate timestamps and symbols, and handle duplicates and missing values explicitly.

## 6. Proposed Technology Stack

- **Python:** Pipeline orchestration, parsing, scoring, and evaluation.
- **Alpaca API:** Market-data acquisition (implemented by the data-collection teammate).
- **Web Search:** News discovery, official-source research, and post-market analysis (implemented by relevant agent/data-collection modules).
- **LLM APIs:** Low-cost models for classification/evaluation; stronger reasoning models for Agents 2 and 3. Keep model choice configurable.
- **Qdrant:** Shared semantic memory.
- **SQLite / PostgreSQL:** Structured data and durable results.
- **Scheduler:** Cron or APScheduler for daily pre-market and post-market runs.
- **Deployment:** Local development first; AWS EC2 with Docker is a reasonable production/demo target.
- **Optional interface:** FastAPI and a small dashboard.

These are architectural preferences, not requirements to implement every dependency immediately.

## 7. Evaluation and Project Success

The system should be measurable, not just produce plausible narratives.

Core measures:
- Directional prediction accuracy against a clearly defined baseline.
- Reasoning support assessments with traceable sources and an *inconclusive* option.
- Accuracy by confidence band and by stock group (Top 10 vs watchlist).
- Cost per daily run and per agent (LLM tokens and paid search calls).
- Latency, retrieval reuse rate, and search calls saved by shared memory.
- Ablations of hotness-score weights and/or model selection when practical.

Avoid claiming a stock movement has a uniquely provable cause. Distinguish observed market outcomes from explanations supported by reporting.

## 8. Development Scope and Team Boundaries

**Current assigned work (data integration / Agent 1 foundation):**
1. Accept teammate-provided `news.txt` and `market_data.csv`.
2. Parse, validate, clean, and persist data.
3. Index relevant news/event evidence in Qdrant and provide retrieval APIs.
4. Calculate market and media-attention features and configurable Hotness Scores.
5. Produce a ranked Top 10 result with evidence references.
6. Supply synthetic fixtures and tests so work can proceed before real API data arrives.

**Other teammates / later stages:** Alpaca API collection, news web-search collection, Agent 2 research, Agent 3 prediction, Agent 4 evaluation, scheduling/UI integration.

Do not implement unrelated modules merely because they appear in this overview. Maintain clean contracts so teammates can integrate their components independently.

## 9. Non-Negotiable Design Rules

- Top **10**, not Top 25.
- Seven **calendar** days for active news/event eligibility; older evidence may remain stored for historical analysis.
- No Google Trends dependency.
- Shared memory is **memory first, web second**, subject to freshness and evidence sufficiency.
- Use code for numeric features, ranking, and direction correctness; LLMs for semantic understanding and reasoning.
- Every claim used in research or reasoning evaluation should be traceable to evidence.
- No future information in predictions; immutable cutoff snapshots.
- No automatic order execution or portfolio trading.
- Keep API credentials in environment variables, never in committed files.
- Prefer small, testable modules and explicit data schemas over an unnecessarily complex framework.
