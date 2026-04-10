"""
Alpha Vantage weekly-adjusted backfill for portfolio stocks.

Why this exists
---------------
Until 2026-04-08 the price_history table was populated from RapidAPI
historical_data, which has two fatal problems for ML:
  1. It returns only a single close price per bar — the existing fetcher at
     src/services/rapidApiService.js copies `price` into open/high/low/close,
     so every row is a flat OHLC bar.
  2. For period>=3yr it silently downsamples to WEEKLY, while for period<=1yr
     it returns daily bars. Historical ingests using period=10yr produced
     weekly cadence; ongoing ingests using period=1yr produced daily cadence.
     The cut-over happened on 2025-03-17. Result: 9 years of weekly rows
     followed by 13 months of daily rows in the same table — breaking every
     row-position feature and label in the ML trainer.

This script replaces the "wrong" portion of price_history with
Alpha Vantage TIME_SERIES_WEEKLY_ADJUSTED data, which gives:
  - Real OHLCV (not flat)
  - Dividend-adjusted close (stored in BOTH `close` and `adj_close`)
  - 20+ years of consistent weekly cadence, free tier
  - Zero coverage for very-new listings like JIOFIN/TMCV — those are
    skipped (kept as-is) per the "only delete wrong data" rule.

Safety
------
  - Backs up every row it is about to delete to data/price_history_backup_*.json
  - Delete is scoped WHERE symbol=? AND date BETWEEN first_av AND last_av,
    so any row outside AV's coverage window is preserved untouched.
  - --dry-run prints the plan without touching Turso.

Run
---
    python3 -m quant_engine.data.av_weekly_backfill                 # backfill all portfolio stocks
    python3 -m quant_engine.data.av_weekly_backfill --dry-run       # preview only
    python3 -m quant_engine.data.av_weekly_backfill --symbol INFY   # single symbol
"""
import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
import datetime as dt
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.turso_client import connect, TursoConnection


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

AV_URL = "https://www.alphavantage.co/query"

# 15 portfolio stocks. Index symbols (^NSEI, ^BSESN) stay as-is — they're
# loaded from sector_indices, not from price_history, for the ML trainer.
PORTFOLIO_SYMBOLS = [
    "ADANIPOWER", "APLLTD", "AWL", "BAJAJHIND", "BANDHANBNK", "ETERNAL",
    "INFY", "JIOFIN", "REPCOHOME", "TANLA", "TATAELXSI", "TATAPOWER",
    "TATASTEEL", "TMCV", "TMPV",
]

# Minimum number of AV bars required before we accept the fetch and replace
# existing data. Guards against API hiccups and very-new listings where the
# backfill would make things worse.
MIN_AV_BARS = 50

# Free-tier rate limit: 5 req/min/key, 25 req/day/key. Rotate every 4 calls to
# stay well under the per-minute cap.
ROTATE_EVERY = 4
RATE_LIMIT_SLEEP = 1.3  # seconds between calls


# ── Alpha Vantage fetch ──────────────────────────────────────────────────────

def _load_keys() -> list[str]:
    raw = os.getenv("ALPHAVANTAGE_KEYS") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("ALPHAVANTAGE_KEYS not set in .env")
    return keys


def _call(params: dict, api_key: str) -> dict:
    qs = urllib.parse.urlencode({**params, "apikey": api_key})
    req = urllib.request.Request(
        f"{AV_URL}?{qs}",
        headers={"User-Agent": "curl/8"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_weekly_adjusted(symbol: str, api_key: str) -> list[dict]:
    """
    Fetch TIME_SERIES_WEEKLY_ADJUSTED for one symbol.
    Returns a list of dicts sorted by date ascending, each with:
        date, open, high, low, close, adj_close, volume

    `close` is populated with the dividend-adjusted value so the ML trainer
    (which reads close directly) gets clean returns. `adj_close` also
    stores the adjusted close for any downstream code that reads it.
    """
    av_symbol = f"{symbol}.BSE"
    data = _call(
        {
            "function": "TIME_SERIES_WEEKLY_ADJUSTED",
            "symbol":   av_symbol,
            "datatype": "json",
        },
        api_key,
    )

    if "Information" in data:
        raise RuntimeError(f"{symbol}: AV info: {data['Information'][:120]}")
    if "Error Message" in data:
        raise RuntimeError(f"{symbol}: AV error: {data['Error Message'][:120]}")
    if "Weekly Adjusted Time Series" not in data:
        raise RuntimeError(f"{symbol}: unexpected AV response keys: {list(data.keys())}")

    ts = data["Weekly Adjusted Time Series"]
    rows: list[dict] = []
    for d, v in ts.items():
        raw_close = float(v["4. close"])
        adj_close = float(v["5. adjusted close"])
        # Dividend-adjust open/high/low by the same ratio so returns stay consistent.
        # This is the standard adjustment: adj_factor = adj_close / raw_close.
        if raw_close > 0:
            f = adj_close / raw_close
        else:
            f = 1.0
        rows.append({
            "date":      d,
            "open":      round(float(v["1. open"]) * f, 4),
            "high":      round(float(v["2. high"]) * f, 4),
            "low":       round(float(v["3. low"])  * f, 4),
            "close":     round(adj_close, 4),
            "adj_close": round(adj_close, 4),
            "volume":    int(float(v["6. volume"])),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


# ── DB operations ────────────────────────────────────────────────────────────

def backup_existing(conn: TursoConnection, symbols: list[str]) -> Path:
    """Dump every price_history row for the given symbols to a JSON file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("data") / f"price_history_backup_{ts}.json"
    out.parent.mkdir(exist_ok=True)

    backup: dict[str, list[dict]] = {}
    for sym in symbols:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, adj_close "
            "FROM price_history WHERE symbol = ? ORDER BY date",
            (sym,),
        ).fetchall()
        backup[sym] = [
            {"date": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": r[5], "adj_close": r[6]}
            for r in rows
        ]
        logger.info("backup: %-12s %d rows", sym, len(rows))

    out.write_text(json.dumps(backup, indent=2))
    logger.info("backup written to %s (%d symbols)", out, len(backup))
    return out


def existing_range(conn: TursoConnection, symbol: str) -> tuple[Optional[str], Optional[str], int]:
    r = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM price_history WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if r is None or r[2] is None:
        return None, None, 0
    return r[0], r[1], int(r[2])


def replace_symbol(
    conn: TursoConnection,
    symbol: str,
    av_rows: list[dict],
    dry_run: bool,
) -> dict:
    """
    Replace price_history rows for `symbol` in AV's date range with AV rows.

    - Deletes WHERE symbol=? AND date BETWEEN first_av AND last_av (inclusive)
    - Preserves any rows outside that window
    - Inserts AV rows (date is UNIQUE(symbol, date), so any dedupe is implicit)

    Returns a report dict.
    """
    if not av_rows:
        return {"symbol": symbol, "action": "skip", "reason": "no av rows"}

    first_av = av_rows[0]["date"]
    last_av  = av_rows[-1]["date"]

    existing_min, existing_max, existing_n = existing_range(conn, symbol)
    in_range_n = conn.execute(
        "SELECT COUNT(*) FROM price_history WHERE symbol = ? "
        "AND date BETWEEN ? AND ?",
        (symbol, first_av, last_av),
    ).fetchone()[0] or 0

    report = {
        "symbol":            symbol,
        "action":            "replace",
        "av_bars":           len(av_rows),
        "av_range":          [first_av, last_av],
        "existing_range":    [existing_min, existing_max, existing_n],
        "existing_in_range": int(in_range_n),
        "to_delete":         int(in_range_n),
        "to_insert":         len(av_rows),
        "preserved_outside_range": int(existing_n - in_range_n),
    }

    if dry_run:
        report["action"] = "dry-run"
        return report

    # 1) delete within AV window
    conn.execute(
        "DELETE FROM price_history WHERE symbol = ? AND date BETWEEN ? AND ?",
        (symbol, first_av, last_av),
    )

    # 2) insert AV rows
    insert_sql = (
        "INSERT OR REPLACE INTO price_history "
        "(symbol, date, open, high, low, close, volume, adj_close) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params_list = [
        (symbol, r["date"], r["open"], r["high"], r["low"],
         r["close"], r["volume"], r["adj_close"])
        for r in av_rows
    ]
    # Chunk to avoid oversized HTTP payloads
    CHUNK = 200
    for i in range(0, len(params_list), CHUNK):
        conn.executemany(insert_sql, params_list[i:i + CHUNK])

    return report


# ── Downsample uncovered symbols (JIOFIN, TMCV) to weekly ───────────────────

# Symbols that AV doesn't cover and must be downsampled from daily ingest bars.
# These are kept separate from PORTFOLIO_SYMBOLS so the AV fetch loop skips them.
UNCOVERED_SYMBOLS = ["JIOFIN", "TMCV"]


def _last_complete_friday() -> str:
    """
    Return the date string of the most recent Friday that has fully passed.

    'Fully passed' means we are now past that calendar date, so the weekly bar
    for that week is complete and will not gain new data.  The current week is
    always excluded — even on Fridays — because we cannot know whether the
    trading session has ended.

    Examples
    --------
    today = Thu 2026-04-09  →  last complete Friday = 2026-04-03
    today = Fri 2026-04-10  →  last complete Friday = 2026-04-03  (current week excluded)
    today = Mon 2026-04-13  →  last complete Friday = 2026-04-10  (last week now complete)
    """
    today = dt.date.today()
    # weekday(): Mon=0 … Fri=4 … Sun=6
    # days_since_friday: Fri=0, Sat=1, Sun=2, Mon=3, Tue=4, Wed=5, Thu=6
    days_since_friday = (today.weekday() - 4) % 7
    if days_since_friday == 0:
        # Today is Friday — exclude the current week, go back 7 more days
        return (today - dt.timedelta(days=7)).isoformat()
    return (today - dt.timedelta(days=days_since_friday)).isoformat()


def downsample_uncovered_to_weekly(
    conn: TursoConnection,
    symbols: Optional[list] = None,
    dry_run: bool = False,
) -> list[dict]:
    """
    Resample daily price_history rows to weekly (W-FRI) for symbols that Alpha
    Vantage does not cover (default: UNCOVERED_SYMBOLS).

    Safety guards
    -------------
    - Only resamples through the last COMPLETE Friday (_last_complete_friday).
      This prevents creating a partial-week bar labeled with a future date —
      the root cause of the 2026-04-10 bogus bar for JIOFIN/TMCV.
    - Deletes existing rows in [first_resampled_date, cutoff] before inserting,
      so daily stubs from the incremental ingest are replaced cleanly.
    - Rows before the first resampled date are preserved untouched.
    - dry_run=True prints the plan without touching the DB.
    """
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas is required for downsample_uncovered_to_weekly")

    if symbols is None:
        symbols = UNCOVERED_SYMBOLS

    cutoff = _last_complete_friday()
    logger.info("downsample cutoff (last complete Friday): %s", cutoff)

    reports = []
    for sym in symbols:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM price_history "
            "WHERE symbol = ? AND date <= ? "
            "ORDER BY date",
            (sym, cutoff),
        ).fetchall()

        if not rows:
            logger.warning("downsample %s: no rows up to %s — skip", sym, cutoff)
            reports.append({"symbol": sym, "action": "skip", "reason": "no rows"})
            continue

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        weekly = df.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])

        # Paranoia check: confirm no label is beyond our cutoff
        beyond = weekly[weekly.index.date > dt.date.fromisoformat(cutoff)]
        if not beyond.empty:
            logger.error(
                "downsample %s: resample produced bars beyond cutoff %s: %s — aborting",
                sym, cutoff, beyond.index.tolist()
            )
            reports.append({"symbol": sym, "action": "error", "reason": "bars beyond cutoff"})
            continue

        first_weekly = weekly.index[0].date().isoformat()
        last_weekly  = weekly.index[-1].date().isoformat()
        n_weekly     = len(weekly)

        report = {
            "symbol":     sym,
            "action":     "dry-run" if dry_run else "resample",
            "cutoff":     cutoff,
            "range":      [first_weekly, last_weekly],
            "n_weekly":   n_weekly,
            "n_daily_in": len(rows),
        }

        logger.info(
            "%s %-8s  daily=%d → weekly=%d  range=%s..%s  cutoff=%s",
            "[dry]" if dry_run else "[run]",
            sym, len(rows), n_weekly, first_weekly, last_weekly, cutoff,
        )

        if not dry_run:
            # Delete existing rows in the resampled window (removes daily stubs)
            conn.execute(
                "DELETE FROM price_history WHERE symbol = ? AND date BETWEEN ? AND ?",
                (sym, first_weekly, last_weekly),
            )

            # Clean up any partial-week resample artifacts beyond the cutoff.
            # These are bars dated > cutoff where open != close (OHLC spread),
            # which is the fingerprint of a prior W-FRI resample bar.  Daily
            # ingest bars (RapidAPI) are always flat (open=high=low=close), so
            # this predicate never removes valid ingest data.
            deleted_artifacts = conn.execute(
                "DELETE FROM price_history "
                "WHERE symbol = ? AND date > ? AND open != close",
                (sym, cutoff),
            ).rowcount
            if deleted_artifacts:
                logger.info(
                    "downsample %s: removed %d stale resample artifact(s) beyond cutoff",
                    sym, deleted_artifacts,
                )

            # Insert weekly rows
            weekly_rows = [
                (sym,
                 row.Index.date().isoformat(),
                 round(float(row.open),  4),
                 round(float(row.high),  4),
                 round(float(row.low),   4),
                 round(float(row.close), 4),
                 int(row.volume))
                for row in weekly.itertuples()
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO price_history "
                "(symbol, date, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                weekly_rows,
            )
            logger.info("downsample %s: inserted %d weekly bars", sym, len(weekly_rows))

        reports.append(report)

    return reports


# ── Driver ───────────────────────────────────────────────────────────────────

def run(symbols: list[str], dry_run: bool = False) -> dict:
    keys = _load_keys()
    conn = connect()

    if not dry_run:
        backup_existing(conn, symbols)

    overall: dict = {"symbols": [], "skipped": [], "errors": []}
    key_idx = 0

    for i, sym in enumerate(symbols):
        if i > 0 and i % ROTATE_EVERY == 0:
            key_idx = (key_idx + 1) % len(keys)
        key = keys[key_idx]

        try:
            av_rows = fetch_weekly_adjusted(sym, key)
        except Exception as exc:
            logger.warning("skip %s: %s", sym, exc)
            overall["skipped"].append({"symbol": sym, "reason": str(exc)[:200]})
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        if len(av_rows) < MIN_AV_BARS:
            logger.warning(
                "skip %s: only %d bars from AV (< MIN_AV_BARS=%d)",
                sym, len(av_rows), MIN_AV_BARS,
            )
            overall["skipped"].append({
                "symbol": sym,
                "reason": f"only {len(av_rows)} bars (< {MIN_AV_BARS})",
            })
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        try:
            report = replace_symbol(conn, sym, av_rows, dry_run=dry_run)
            logger.info(
                "%s %-12s  av=%d  delete=%d  insert=%d  preserved=%d  range=%s..%s",
                "[dry]" if dry_run else "[run]",
                sym,
                report["av_bars"],
                report["to_delete"],
                report["to_insert"],
                report["preserved_outside_range"],
                report["av_range"][0],
                report["av_range"][1],
            )
            overall["symbols"].append(report)
        except Exception as exc:
            logger.exception("failed %s", sym)
            overall["errors"].append({"symbol": sym, "error": str(exc)[:200]})

        time.sleep(RATE_LIMIT_SLEEP)

    return overall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + show plan without modifying price_history",
    )
    ap.add_argument(
        "--symbol", default=None,
        help="Process only this one symbol (must be in PORTFOLIO_SYMBOLS)",
    )
    ap.add_argument(
        "--resample-uncovered", action="store_true",
        help=(
            "Downsample JIOFIN/TMCV daily bars to weekly (W-FRI) up to the "
            "last complete Friday.  Mutually exclusive with --symbol AV fetch."
        ),
    )
    args = ap.parse_args()

    if args.resample_uncovered:
        conn = connect()
        reports = downsample_uncovered_to_weekly(conn, dry_run=args.dry_run)
        conn.close()
        for r in reports:
            print(r)
        sys.exit(0)

    symbols = PORTFOLIO_SYMBOLS
    if args.symbol:
        if args.symbol not in PORTFOLIO_SYMBOLS:
            logger.error("symbol %s not in PORTFOLIO_SYMBOLS", args.symbol)
            sys.exit(2)
        symbols = [args.symbol]

    result = run(symbols, dry_run=args.dry_run)

    print("\n=== Summary ===")
    print(f"Processed: {len(result['symbols'])}/{len(symbols)}")
    print(f"Skipped:   {len(result['skipped'])}")
    print(f"Errors:    {len(result['errors'])}")
    if result["skipped"]:
        print("\nSkipped:")
        for s in result["skipped"]:
            print(f"  {s['symbol']}: {s['reason']}")
    if result["errors"]:
        print("\nErrors:")
        for e in result["errors"]:
            print(f"  {e['symbol']}: {e['error']}")

    # Persist the run report for later inspection
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path("data") / f"av_backfill_report_{ts}.json"
    report_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
