"""
Delivery-spike event study (2026-06-10 strategy review, item "delivery-spike").

Hypothesis: an abnormal spike in delivery percentage (shares actually taken
into demat vs intraday churn) marks institutional accumulation/distribution
and predicts drift. Documented as an NSE anomaly in Indian-market
practitioner literature; the project has only ever used delivery_pct as an
ML z-score feature, never as a standalone event signal.

DECLARED RULE:
  spike      = delivery_pct z-score > +2 vs trailing 60 sessions
               (min 30 observations; z uses that symbol's own history)
  cohorts    = spike × same-day price direction:
                 SPIKE_UP   — z > 2 AND close-to-close return > +1%
                              (accumulation with price confirmation)
                 SPIKE_DOWN — z > 2 AND return < −1%
                              (distribution into weakness)
  entry      = close(t+1); outcome = stock − NIFTY abnormal return to
               t+1+h, h ∈ {1, 5, 10, 20}; monthly-clustered t.
  ship bar   = |t| > 3 AND drift > 40bp at the horizon, either cohort.

Data: delivery_data (NSE MTO archives, backfilled 2019→today for the full
~330-symbol universe on 2026-06-10) joined to price_history per symbol.

Usage:
    python3 -m quant_engine.research.delivery_spike [--from-date 2019-01-01]

Output: data/delivery_spike.json + console summary.
"""
import argparse
import json
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quant_engine.data.loader import load_price_history, get_connection
from quant_engine.research.bulk_deal_drift import _summarize, HORIZONS

logger = logging.getLogger(__name__)

Z_MIN = 2.0
Z_WINDOW = 60
Z_MIN_OBS = 30
RET_CONFIRM = 0.01
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "delivery_spike.json"


def _symbol_events(symbol: str, delivery: pd.DataFrame, nifty_close: pd.Series,
                   from_date: str, registry=None) -> list[dict]:
    df = load_price_history(symbol, limit=None)
    if df.empty or len(df) < 100:
        return []
    close = df["close"].astype(float)
    ret1 = close.pct_change()

    dpct = delivery.set_index("date")["delivery_pct"].astype(float)
    dpct.index = pd.to_datetime(dpct.index)
    dpct = dpct[~dpct.index.duplicated(keep="last")].reindex(close.index)

    mean = dpct.rolling(Z_WINDOW, min_periods=Z_MIN_OBS).mean()
    std = dpct.rolling(Z_WINDOW, min_periods=Z_MIN_OBS).std().replace(0, np.nan)
    z = (dpct - mean) / std

    idx = close.index
    out = []
    spikes = (z > Z_MIN) & (idx >= pd.Timestamp(from_date))
    # PIT filter: only event dates where the symbol was an index member —
    # the June audit showed survivorship inflates BUY-side event results.
    if registry is not None:
        member = pd.Series(
            [registry.contains(symbol, d.date()) for d in idx], index=idx
        )
        spikes = spikes & member
    for pos in np.flatnonzero(spikes.to_numpy()):
        r = ret1.iloc[pos]
        if pd.isna(r) or abs(r) < RET_CONFIRM:
            continue
        entry = pos + 1
        if entry + max(HORIZONS) >= len(idx):
            continue
        entry_date = idx[entry]
        if entry_date not in nifty_close.index:
            continue
        npos = nifty_close.index.searchsorted(entry_date)
        rec = {"symbol": symbol, "date": str(idx[pos].date()),
               "deal_type": "DELIVERY", "side": "UP" if r > 0 else "DOWN",
               "z": float(z.iloc[pos]), "day_ret": float(r)}
        for h in HORIZONS:
            stock_ret = close.iloc[entry + h] / close.iloc[entry] - 1
            nx = min(npos + h, len(nifty_close) - 1)
            mkt_ret = nifty_close.iloc[nx] / nifty_close.iloc[npos] - 1
            rec[f"ar{h}"] = float(stock_ret - mkt_ret)
        out.append(rec)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2019-01-01")
    parser.add_argument("--pit", action="store_true",
                        help="Restrict events to dates of NIFTY200 membership")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    registry = None
    if args.pit:
        from quant_engine.data.membership import MembershipRegistry
        conn = get_connection()
        try:
            registry = MembershipRegistry.from_turso(conn)
        finally:
            conn.close()
        logger.info("PIT filter active (%d symbols ever members)",
                    len(registry.all_symbols()))

    conn = get_connection()
    try:
        delivery = pd.read_sql_query(
            "SELECT symbol, date, delivery_pct FROM delivery_data "
            "WHERE delivery_pct IS NOT NULL AND delivery_pct > 0",
            conn,
        )
    finally:
        conn.close()
    logger.info("Delivery rows: %d across %d symbols, %s → %s",
                len(delivery), delivery["symbol"].nunique(),
                delivery["date"].min(), delivery["date"].max())

    nifty = load_price_history("^NSEI", limit=None)
    nifty_close = nifty["close"].astype(float)

    symbols = sorted(delivery["symbol"].unique())
    records = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_symbol_events, s, delivery[delivery["symbol"] == s],
                        nifty_close, args.from_date, registry): s
            for s in symbols
        }
        for fut in as_completed(futures):
            try:
                records.extend(fut.result())
            except Exception as exc:
                logger.warning("symbol %s failed: %s", futures[fut], exc)

    ar = pd.DataFrame(records)
    logger.info("Spike events: %d", len(ar))

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "pit": bool(args.pit),
        "rule": {"z_min": Z_MIN, "z_window": Z_WINDOW, "ret_confirm": RET_CONFIRM},
        "n_events": int(len(ar)),
        "cohorts": {},
        "ship_bar": "|t_monthly| > 3 AND drift > 40bp, either cohort",
    }
    print(f"\n{'cohort':14s} {'n':>6s}   " + "   ".join(f"{h}d: AR / t / hit" for h in HORIZONS))
    for side, grp in ar.groupby("side"):
        name = f"SPIKE_{side}"
        s = _summarize(grp)
        results["cohorts"][name] = s
        if "1d" in s:
            cells = [f"{s[f'{h}d']['mean_ar_bp']:+7.1f}/{s[f'{h}d']['t_monthly']:+.1f}/{s[f'{h}d']['hit_pct']:.0f}%"
                     for h in HORIZONS]
            print(f"{name:14s} {s['n_events']:>6d}   " + "   ".join(cells))
        else:
            print(f"{name:14s} {s['n_events']:>6d}   insufficient")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
