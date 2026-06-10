"""
Bulk/block-deal drift event study (2026-06-10 strategy review, item #3a).

Question: do NSE bulk/block deals predict post-disclosure drift? Bulk deals
(client crosses 0.5% of equity in a day) and block deals (negotiated crosses)
are disclosed by NSE after market close — the public information event is the
evening of the deal date, tradeable from the next session.

DECLARED RULE (fixed up front — 2 sides × 2 deal types × 4 horizons; the
splits are reported jointly, not selected over):

  Event       = (symbol, date) where one side dominates the day's deal flow:
                dominance = (buy_value − sell_value) / (buy_value + sell_value)
                BUY event if dominance > +0.5, SELL event if < −0.5.
                (Same-day churners — quant desks crossing both sides — net
                 out and are excluded by the dominance filter.)
  Entry       = close of t+1 (first tradeable close after disclosure).
  Outcome     = market-adjusted drift: stock return − NIFTY (^NSEI) return,
                from close(t+1) to close(t+1+h), h ∈ {1, 5, 10, 20}.
  Coverage    = symbols present in price_history with a bar on t+1.
  Inference   = mean abnormal return, t-stat clustered by event month.
  Ship bar    = |t| > 3 AND drift at the horizon exceeds ~40bp round-trip
                cost, on the BUY side (the only side a long-only book can act
                on directly).

Usage:
    python3 -m quant_engine.research.bulk_deal_drift [--from-date 2019-01-01]

Output: data/bulk_deal_drift.json + console summary.
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

logger = logging.getLogger(__name__)

HORIZONS = (1, 5, 10, 20)
DOMINANCE_MIN = 0.5
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "bulk_deal_drift.json"


def load_events(from_date: str) -> pd.DataFrame:
    """Aggregate raw deals into per-(symbol, date, deal_type) dominance events."""
    conn = get_connection()
    try:
        deals = pd.read_sql_query(
            """
            SELECT date, symbol, trade_type, quantity, price, deal_type
            FROM bulk_block_deals
            WHERE date >= ? AND date NOT LIKE '%RECORD%'
              AND quantity > 0 AND price > 0
            """,
            conn, params=(from_date,),
        )
    finally:
        conn.close()

    deals["value"] = deals["quantity"] * deals["price"]
    deals["side"] = deals["trade_type"].str.upper().str.strip()
    deals = deals[deals["side"].isin(["BUY", "SELL"])]

    g = deals.pivot_table(
        index=["symbol", "date", "deal_type"], columns="side", values="value",
        aggfunc="sum", fill_value=0.0,
    ).reset_index()
    for side in ("BUY", "SELL"):
        if side not in g.columns:
            g[side] = 0.0
    gross = g["BUY"] + g["SELL"]
    g = g[gross > 0]
    g["dominance"] = (g["BUY"] - g["SELL"]) / (g["BUY"] + g["SELL"])
    g["event_side"] = np.where(g["dominance"] > DOMINANCE_MIN, "BUY",
                       np.where(g["dominance"] < -DOMINANCE_MIN, "SELL", None))
    g = g[g["event_side"].notna()]
    g["gross_value_cr"] = (g["BUY"] + g["SELL"]) / 1e7
    return g[["symbol", "date", "deal_type", "event_side", "dominance", "gross_value_cr"]]


def _abnormal_returns(symbol: str, sym_events: pd.DataFrame, nifty_close: pd.Series) -> list[dict]:
    df = load_price_history(symbol, limit=None)
    if df.empty or len(df) < 40:
        return []
    close = df["close"].astype(float)
    idx = close.index
    out = []
    for _, ev in sym_events.iterrows():
        ev_date = pd.Timestamp(ev["date"])
        pos = idx.searchsorted(ev_date)
        if pos >= len(idx) or idx[pos] != ev_date:
            continue  # no bar on deal date — skip rather than guess alignment
        entry = pos + 1
        if entry + max(HORIZONS) >= len(idx):
            continue
        entry_date = idx[entry]
        if entry_date not in nifty_close.index:
            continue
        rec = {"symbol": symbol, "date": str(ev["date"]),
               "deal_type": ev["deal_type"], "side": ev["event_side"],
               "gross_value_cr": float(ev["gross_value_cr"])}
        npos = nifty_close.index.searchsorted(entry_date)
        for h in HORIZONS:
            exit_i = entry + h
            stock_ret = close.iloc[exit_i] / close.iloc[entry] - 1
            nx = min(npos + h, len(nifty_close) - 1)
            mkt_ret = nifty_close.iloc[nx] / nifty_close.iloc[npos] - 1
            rec[f"ar{h}"] = float(stock_ret - mkt_ret)
        out.append(rec)
    return out


def _summarize(events: pd.DataFrame) -> dict:
    if len(events) < 50:
        return {"n_events": int(len(events)), "note": "insufficient"}
    out = {"n_events": int(len(events))}
    months = events["date"].str[:7]
    for h in HORIZONS:
        col = f"ar{h}"
        monthly = events.groupby(months)[col].mean()
        t = float(monthly.mean() / (monthly.std(ddof=1) / np.sqrt(len(monthly))))
        out[f"{h}d"] = {
            "mean_ar_bp": round(float(events[col].mean() * 1e4), 1),
            "median_ar_bp": round(float(events[col].median() * 1e4), 1),
            "hit_pct": round(float((events[col] > 0).mean() * 100), 1),
            "t_monthly": round(t, 2),
            "n_months": int(len(monthly)),
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2019-01-01")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    events = load_events(args.from_date)
    logger.info("Dominance events: %d (%s)", len(events),
                events.groupby(["deal_type", "event_side"]).size().to_dict())

    nifty = load_price_history("^NSEI", limit=None)
    nifty_close = nifty["close"].astype(float)

    symbols = sorted(events["symbol"].unique())
    logger.info("Symbols with events: %d — computing abnormal returns…", len(symbols))
    records = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_abnormal_returns, s, events[events["symbol"] == s], nifty_close): s
            for s in symbols
        }
        for fut in as_completed(futures):
            try:
                records.extend(fut.result())
            except Exception as exc:
                logger.warning("symbol %s failed: %s", futures[fut], exc)

    ar = pd.DataFrame(records)
    logger.info("Events with price coverage: %d / %d", len(ar), len(events))

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "from_date": args.from_date,
        "dominance_min": DOMINANCE_MIN,
        "n_raw_events": int(len(events)),
        "n_covered_events": int(len(ar)),
        "cohorts": {},
        "ship_bar": "BUY side: |t_monthly| > 3 AND mean drift > 40bp at the horizon",
    }
    print(f"\n{'cohort':22s} {'n':>6s}   " + "   ".join(f"{h}d: AR / t / hit" for h in HORIZONS))
    for (dt, side), grp in ar.groupby(["deal_type", "side"]):
        name = f"{dt}_{side}"
        s = _summarize(grp)
        results["cohorts"][name] = s
        if "1d" in s:
            cells = [f"{s[f'{h}d']['mean_ar_bp']:+7.1f}/{s[f'{h}d']['t_monthly']:+.1f}/{s[f'{h}d']['hit_pct']:.0f}%"
                     for h in HORIZONS]
            print(f"{name:22s} {s['n_events']:>6d}   " + "   ".join(cells))
        else:
            print(f"{name:22s} {s['n_events']:>6d}   insufficient")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
