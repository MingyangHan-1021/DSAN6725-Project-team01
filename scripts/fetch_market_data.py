"""Download S&P 500 daily bars into the Agent 1 market CSV format, then cut the model input windows.

Run with Python 3.11+ from any directory:

    python scripts/fetch_market_data.py
    python scripts/fetch_market_data.py --source yfinance

Each run downloads the 240 calendar days ending at the latest weekday before today (New York) and writes:
  data/research/  the full download: <date>_past_240_days.csv plus a JSON manifest of the same name
  data/model/     the last 30, 15 and 7 trading days of that download as <date>_past_30_days.csv,
                  <date>_past_15_days.csv and <date>_past_7_days.csv, plus <date>_manifest.json.
                  The three overlap and are alternatives: feed exactly one of them to the model.
Only tickers that traded normally over the whole download (a bar on every trading day) are kept; new listings
and halted stocks drop out, and the research manifest lists them with the reason.
Windows count trading days, not calendar days, so even the 7-day file holds a bar at least seven calendar
days before its latest bar, which Agent 1's 7-day move feature needs.

Sources:
  alpaca (default)  Free Alpaca account; standard library only. Credentials come from the
                    environment or the project .env file (ignored by Git):
                        ALPACA_API_KEY=...
                        ALPACA_SECRET_KEY=...
  yfinance          No account. Needs `pip install yfinance==1.7.0`, or run through
                    `uv run --python 3.11 --with yfinance==1.7.0 python scripts/fetch_market_data.py ...`.
                    Yahoo prices are split-adjusted, so a rerun after a later split can differ.

<date> is the New York download date. A second run on the same date stops instead of overwriting.
Pass the manifest's retrieved_at as `main.py ingest --retrieved-at` when a file is ingested.
"""
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required (the README asks for 3.11+)")

import argparse
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stock_agent.ingestion import MARKET_FIELDS, parse_market  # noqa: E402
from stock_agent.models import Bar, DataError  # noqa: E402

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
RESEARCH_DIR, MODEL_DIR = ROOT / "data/research", ROOT / "data/model"
LOOKBACK_DAYS = 240  # calendar days, ending at the latest weekday before today
MODEL_WINDOWS = (30, 15, 7)  # trading days
DEFAULT_TICKERS = ROOT / "config/sp500_tickers.txt"
TIMEFRAME, SESSION = "1Day", "regular"
SYMBOLS_PER_REQUEST = 100
PAGE_LIMIT = 10000
YFINANCE_VERSION = "1.7.0"

# A record is (trading day, open, high, low, close, volume) before contract validation.
Record = tuple[date, Decimal, Decimal, Decimal, Decimal, object]

def eastern_offset(day: date) -> str:
    """New York UTC offset at 16:00 on `day`; DST runs from the 2nd Sunday of March to the 1st Sunday of November."""
    march = date(day.year, 3, 8)
    november = date(day.year, 11, 1)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)
    return "-04:00" if dst_start <= day < dst_end else "-05:00"

def close_timestamp(day: date) -> str:
    # Agreed convention: a daily regular-session bar is stamped at the 16:00 New York close.
    return f"{day.isoformat()}T16:00:00{eastern_offset(day)}"

def new_york_date(moment: datetime) -> date:
    moment = moment.astimezone(timezone.utc)
    hours = 4 if eastern_offset(moment.date()) == "-04:00" else 5
    return (moment - timedelta(hours=hours)).date()

def default_end(now: datetime) -> date:
    """Most recent weekday before today in New York, so that day's session and after-hours are finished."""
    day = new_york_date(now) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day

def load_env(path: Path) -> None:
    """Read KEY=VALUE lines from .env without overriding variables that are already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

def read_tickers(path: Path) -> list[str]:
    tickers = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if not tickers or len(set(tickers)) != len(tickers):
        raise DataError(f"{path}: ticker list is empty or has duplicates")
    return tickers

def get_json(url: str, headers: dict[str, str]) -> dict:
    """GET with a few retries for rate limits and transient errors; prices parse as exact Decimals."""
    for attempt in range(5):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                return json.load(response, parse_float=Decimal)
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(3 * 2 ** attempt)
                continue
            detail = exc.read().decode(errors="replace")[:300]
            hint = " (check ALPACA_API_KEY / ALPACA_SECRET_KEY)" if exc.code in (401, 403) else ""
            raise DataError(f"Alpaca HTTP {exc.code}{hint}: {detail}") from exc
        except URLError as exc:
            if attempt < 4:
                time.sleep(3 * 2 ** attempt)
                continue
            raise DataError(f"Alpaca unavailable: {exc.reason}") from exc
    raise DataError("Alpaca request failed after retries")

def fetch_alpaca(tickers: list[str], start: date, end: date) -> tuple[dict[str, list[Record]], int, dict]:
    load_env(ROOT / ".env")
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise DataError("set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment or in the project .env")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    records: dict[str, list[Record]] = defaultdict(list)
    calls = 0
    for index in range(0, len(tickers), SYMBOLS_PER_REQUEST):
        params = {"symbols": ",".join(tickers[index:index + SYMBOLS_PER_REQUEST]), "timeframe": TIMEFRAME,
                  "start": f"{start.isoformat()}T00:00:00Z", "end": f"{end.isoformat()}T23:59:59Z",
                  "feed": "sip", "adjustment": "raw", "limit": PAGE_LIMIT}
        token = None
        while True:
            page = get_json(BARS_URL + "?" + urlencode(dict(params, page_token=token) if token else params), headers)
            calls += 1
            for symbol, bars in (page.get("bars") or {}).items():
                for bar in bars:
                    opened = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
                    # Alpaca stamps daily bars at New York midnight (04:00Z or 05:00Z); anything else is unexpected.
                    if (opened.hour, opened.minute, opened.second) not in ((4, 0, 0), (5, 0, 0)):
                        raise DataError(f"{symbol}: unexpected daily bar timestamp {bar['t']}")
                    records[symbol].append((opened.date(), *(Decimal(bar[k]) for k in ("o", "h", "l", "c")), bar["v"]))
            token = page.get("next_page_token")
            if not token:
                break
    meta = {"source": BARS_URL, "feed": "sip", "adjustment": "raw (unadjusted)", "price_rounding": "none",
            "volume_note": "Alpaca daily bars take OHLC from the regular session; volume also counts extended-hours trades"}
    return records, calls, meta

def is_missing(value) -> bool:
    try:
        return value is None or math.isnan(float(value))
    except TypeError:  # pandas.NA
        return True

def cents(value: float) -> Decimal:
    # Yahoo serves float32-like prices (336.67 arrives as 336.6700134277344); rounding is monotonic, so OHLC order holds.
    return Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def fetch_yfinance(tickers: list[str], start: date, end: date) -> tuple[dict[str, list[Record]], int, dict]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise DataError(f"yfinance is not installed; run: pip install yfinance=={YFINANCE_VERSION}") from exc
    if yf.__version__ != YFINANCE_VERSION:
        print(f"warning: yfinance {yf.__version__} differs from the pinned {YFINANCE_VERSION}", file=sys.stderr)
    yahoo = {ticker: ticker.replace(".", "-") for ticker in tickers}  # Yahoo writes BRK.B as BRK-B
    records: dict[str, list[Record]] = defaultdict(list)
    batches = 0
    for index in range(0, len(tickers), SYMBOLS_PER_REQUEST):
        batch = tickers[index:index + SYMBOLS_PER_REQUEST]
        frame = yf.download([yahoo[t] for t in batch], start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                            interval="1d", auto_adjust=False, actions=False, group_by="column",
                            progress=False, threads=True)
        batches += 1
        for ticker in batch:
            try:
                bars = frame.xs(yahoo[ticker], axis=1, level=1)
            except (KeyError, IndexError, ValueError):
                continue  # failed or empty download; coverage() lists the ticker as without data
            for stamp, row in bars.iterrows():
                values = [row[column] for column in ("Open", "High", "Low", "Close", "Volume")]
                if any(is_missing(value) for value in values):
                    continue  # no trade reported for this ticker on this date; coverage() lists it as missing
                volume = int(values[4]) if float(values[4]).is_integer() else values[4]
                records[ticker].append((stamp.date(), *(cents(float(v)) for v in values[:4]), volume))
    meta = {"source": f"Yahoo Finance via yfinance {yf.__version__} (download, auto_adjust=False)", "feed": None,
            "adjustment": "split-adjusted by Yahoo; not dividend-adjusted (Close, not Adj Close)",
            "price_rounding": "rounded to cents to remove Yahoo float noise",
            "volume_note": "daily volume as reported by Yahoo",
            "reproducibility_note": "Yahoo rewrites split-adjusted history after each split, so a later rerun can differ"}
    return records, batches, meta

SOURCES = {"alpaca": fetch_alpaca, "yfinance": fetch_yfinance}

def plain(value: Decimal) -> str:
    return format(value.normalize(), "f")

def to_rows(records: dict[str, list[Record]], start: date, end: date) -> tuple[list[list[str]], list[dict]]:
    """Convert records to CSV rows; bars that break the data contract are reported, never repaired."""
    rows: list[list[str]] = []
    rejected: list[dict] = []
    for ticker in sorted(records):
        for day, *prices, volume in sorted(records[ticker], key=lambda record: record[0]):
            if not start <= day <= end:
                continue
            row = [ticker, close_timestamp(day), TIMEFRAME, SESSION, *map(plain, prices), str(volume)]
            try:
                if not isinstance(volume, int):
                    raise DataError(f"volume {volume} is not an integer")
                Bar(ticker, datetime.fromisoformat(row[1]), TIMEFRAME, SESSION, *prices, volume)
            except DataError as exc:
                rejected.append({"ticker": ticker, "date": day.isoformat(), "reason": str(exc), "bar": row[4:]})
                continue
            rows.append(row)
    return rows, rejected

def coverage(rows: list[list[str]], tickers: list[str]) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Trading days are the dates any ticker traded; a ticker lacking one of them has a missing bar."""
    days_by_ticker: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        days_by_ticker[row[0]].add(row[1][:10])
    trading_days = sorted(set().union(*days_by_ticker.values())) if days_by_ticker else []
    without_data = [ticker for ticker in tickers if ticker not in days_by_ticker]
    missing = {ticker: sorted(set(trading_days) - days) for ticker, days in sorted(days_by_ticker.items())
               if set(trading_days) - days}
    return trading_days, without_data, missing

def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def relative(path: Path) -> str:
    path = path.resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)

def write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write through a .partial file that gets its final name only after Agent 1's own parser reads it back."""
    partial = path.with_name(path.name + ".partial")
    try:
        with partial.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(MARKET_FIELDS)
            writer.writerows(rows)
        if len(parse_market(partial)) != len(rows):
            raise DataError(f"{path.name} does not round-trip through parse_market")
        partial.rename(path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

def write_model_splits(rows: list[list[str]], trading_days: list[str], research: dict,
                       csv_paths: dict[int, Path], manifest_path: Path) -> dict:
    """Write the last N trading days of the download for each N in MODEL_WINDOWS, plus one manifest for the set."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    source = research["source_name"]
    splits = []
    for window in MODEL_WINDOWS:
        days = trading_days[-window:]
        kept = set(days)
        subset = [row for row in rows if row[1][:10] in kept]
        csv_path = csv_paths[window]
        write_csv(csv_path, subset)
        splits.append({
            "window_trading_days": window, "csv_file": csv_path.name, "csv_sha256": file_sha256(csv_path),
            "first_bar_date": days[0], "last_bar_date": days[-1], "rows": len(subset),
            "tickers": len({row[0] for row in subset}),
        })
    manifest = {
        "usage": "the splits overlap and are alternatives; feed exactly one of them to the model",
        "window_unit": "trading days: dates on which at least one requested ticker has a bar",
        "universe": research["universe_rule"],
        "retrieved_at": research["retrieved_at"], "source_name": source, "source": research["source"],
        "adjustment": research["adjustment"], "timeframe": TIMEFRAME, "session": SESSION,
        "parent_csv": relative(RESEARCH_DIR / research["csv_file"]), "parent_csv_sha256": research["csv_sha256"],
        "splits": splits,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"manifest": manifest_path.name, **manifest}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Download the last {LOOKBACK_DAYS} days of S&P 500 daily bars and cut the model windows")
    parser.add_argument("--source", choices=sorted(SOURCES), default="alpaca", help="data provider (default: alpaca)")
    parser.add_argument("--tickers", default=str(DEFAULT_TICKERS), help="one ticker per line; # starts a comment")
    args = parser.parse_args(argv)
    try:
        started = datetime.now(timezone.utc)
        end = default_end(started)
        start = end - timedelta(days=LOOKBACK_DAYS - 1)
        tickers_path = Path(args.tickers)
        tickers = read_tickers(tickers_path)

        today = new_york_date(started)
        csv_path = RESEARCH_DIR / f"{today}_past_{LOOKBACK_DAYS}_days.csv"
        manifest_path = csv_path.with_suffix(".json")
        model_csvs = {window: MODEL_DIR / f"{today}_past_{window}_days.csv" for window in MODEL_WINDOWS}
        model_manifest = MODEL_DIR / f"{today}_manifest.json"
        taken = [path for path in (csv_path, manifest_path, *model_csvs.values(), model_manifest) if path.exists()]
        if taken:
            raise DataError(f"today's download already exists; delete these files to download again: "
                            f"{', '.join(relative(path) for path in taken)}")

        records, requests, source_meta = SOURCES[args.source](tickers, start, end)
        retrieved = datetime.now(timezone.utc).replace(microsecond=0)
        rows, rejected = to_rows(records, start, end)
        if not rows:
            raise DataError(f"{args.source} returned no bars between {start} and {end}")
        trading_days, without_data, missing = coverage(rows, tickers)
        if len(trading_days) < max(MODEL_WINDOWS):
            raise DataError(f"only {len(trading_days)} trading days downloaded; the model windows need {max(MODEL_WINDOWS)}")
        # Keep only tickers that traded normally over the whole download: a bar on every trading day.
        excluded = {ticker: "no data" for ticker in without_data}
        excluded.update({ticker: f"no bar on {len(days)} of {len(trading_days)} trading days ({days[0]} to {days[-1]})"
                         for ticker, days in missing.items()})
        rows = [row for row in rows if row[0] not in excluded]
        kept = len(tickers) - len(excluded)

        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        write_csv(csv_path, rows)

        manifest = {
            "csv_file": csv_path.name, "csv_sha256": file_sha256(csv_path),
            "requested_start": start.isoformat(), "requested_end": end.isoformat(), "calendar_days": LOOKBACK_DAYS,
            "first_bar_date": trading_days[0], "last_bar_date": trading_days[-1], "trading_days": len(trading_days),
            "download_started_at": started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "retrieved_at": retrieved.isoformat().replace("+00:00", "Z"),
            "source_name": args.source, **source_meta, "timeframe": TIMEFRAME, "session": SESSION,
            "timestamp_convention": "each bar is stamped at the 16:00 America/New_York close of its trading day",
            "tickers_file": relative(tickers_path), "tickers_file_sha256": file_sha256(tickers_path),
            "universe_rule": "tickers with a bar on every trading day of the download; the rest are excluded",
            "tickers_requested": len(tickers), "tickers_kept": kept, "tickers_excluded": excluded,
            "rows": len(rows), "rejected_bars": rejected, "requests": requests,
            "script": relative(Path(__file__)), "script_sha256": file_sha256(Path(__file__)),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        model = write_model_splits(rows, trading_days, manifest, model_csvs, model_manifest)

        summary = {key: manifest[key] for key in ("csv_file", "rows", "first_bar_date", "last_bar_date", "trading_days",
                                                  "tickers_kept", "tickers_excluded", "retrieved_at", "requests")}
        summary.update(rejected_bars=len(rejected), manifest=manifest_path.name)
        summary["model"] = {"manifest": model["manifest"],
                            "splits": [{key: split[key] for key in ("csv_file", "rows", "tickers")}
                                       for split in model["splits"]]}
        print(json.dumps(summary, indent=2))
        return 0
    except (DataError, OSError, ValueError, KeyError) as exc:
        print(f"fetch_market_data error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
