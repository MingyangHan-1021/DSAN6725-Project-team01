# Market data download

`scripts/fetch_market_data.py` downloads daily OHLCV bars for our stock list and writes the market CSVs that Agent 1 ingests.

## Run

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/). The yfinance source needs no account or API key. Run from the repository root:

```bash
uv run --python 3.11 --with yfinance==1.7.0 python scripts/fetch_market_data.py --source yfinance
```

There are no date arguments. Each run covers the **240 calendar days ending at the latest weekday before today** (New York time), so the current day's unfinished session is never included. A full run takes about 20 seconds.

## Output

`<date>` is the New York date of the download, e.g. `2026-10-12`. Both folders are in the repo, but Git ignores the downloaded files, so each person generates their own data.

| File | Contents |
|---|---|
| `data/research/<date>_past_240_days.csv` | Full download: 500 tickers × about 166 trading days |
| `data/research/<date>_past_240_days.json` | Manifest: source, retrieval time, ticker list checksum, excluded tickers |
| `data/model/<date>_past_30_days.csv` | Last 30 trading days of the full download |
| `data/model/<date>_past_15_days.csv` | Last 15 trading days |
| `data/model/<date>_past_7_days.csv` | Last 7 trading days |
| `data/model/<date>_manifest.json` | Date range, row count, and checksum of each model file |

The three model files overlap, and the model uses **exactly one of them**. Windows count trading days, so even the 7-day file has a bar from at least a week before the latest one, which Agent 1's 7-day move feature needs.

CSV columns are `ticker,timestamp,timeframe,session,open,high,low,close,volume`. Each bar is stamped at the 16:00 New York close, e.g. `2026-10-09T16:00:00-04:00`. Prices are split-adjusted but not dividend-adjusted.

## Ticker list

The tickers come from `config/sp500_tickers.txt`: S&P 500 members that traded normally all year, currently 500. To add or remove a stock, edit that file and update its header comment. The script drops any ticker with a missing day in the 240-day download, such as a stock listed this year, and the research manifest lists it under `tickers_excluded`.

## Notes

- **Same-day rerun:** a second run on the same date stops without overwriting. Delete that date's files first to download again.
- **Ingest into Agent 1:** pass the manifest's `retrieved_at` value, for example:
  ```bash
  python main.py ingest --news <news.txt> --market data/model/<date>_past_7_days.csv --retrieved-at <retrieved_at>
  ```

