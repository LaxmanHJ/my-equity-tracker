"""
PEAD — post-earnings announcement drift event study (SIC-93).

The last untested cross-sectional alpha class after the six 2026-06-10
failures. Events come from `earnings_events` (NSE financial-results
broadcasts with timestamps, backfilled 2019→today by backfill_earnings.py).

DECLARED RULE (fixed before running; from the SIC-93 ticket):

  Event        = first broadcast per (symbol, period_end) — earliest
                 broadcast_ts dedupes consolidated/standalone double-filings
                 and re-filings.
  Reaction day = broadcast ≤ 15:30 IST on a trading day → that session;
                 else the next session with a price bar.
  Surprise     = reaction-day market-adjusted return close(R−1)→close(R)
                 vs ^NSEI, terciled per calendar quarter (announcement-
                 reaction-as-surprise; no consensus history exists).
  Entry        = close(R); outcome = market-adjusted drift R→R+{5,20,60};
                 monthly-clustered t per tercile.
  PIT          = MANDATORY (delivery_spike lesson: the no-PIT version of a
                 fresh signal "passed" at t=3.3 then collapsed to t=0.9).
                 Events only on dates of NIFTY200 membership.
  Ship bar     = top tercile |t| > 3 AND drift > 40bp at the horizon.
                 9 cells reported jointly (3 terciles × 3 horizons); under
                 the null, expected max |t| ≈ 2.5.

Usage:
    python3 -m quant_engine.research.pead_drift

Output: data/pead_drift.json + console summary.
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
from quant_engine.data.membership import MembershipRegistry

logger = logging.getLogger(__name__)

HORIZONS = (5, 20, 60)
MARKET_CLOSE_T = "15:30:00"
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "pead_drift.json"


def load_events() -> pd.DataFrame:
    """First broadcast per (symbol, period_end)."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT symbol, MIN(broadcast_ts) AS broadcast_ts, period_end
            FROM earnings_events
            WHERE period_end IS NOT NULL
            GROUP BY symbol, period_end
            """,
            conn,
        )
    finally:
        conn.close()
    df["broadcast_ts"] = pd.to_datetime(df["broadcast_ts"])
    return df


def _symbol_events(symbol: str, sym_events: pd.DataFrame, nifty_close: pd.Series,
                   registry: MembershipRegistry) -> list[dict]:
    df = load_price_history(symbol, limit=None)
    if df.empty or len(df) < 80:
        return []
    close = df["close"].astype(float)
    idx = close.index

    out = []
    for _, ev in sym_events.iterrows():
        ts = ev["broadcast_ts"]
        # Reaction day: same session iff broadcast before the close on a bar day
        bcast_date = pd.Timestamp(ts.date())
        same_session = (
            ts.time() <= pd.Timestamp(f"2000-01-01 {MARKET_CLOSE_T}").time()
            and bcast_date in idx
        )
        if same_session:
            rpos = idx.get_loc(bcast_date)
        else:
            rpos = idx.searchsorted(bcast_date + pd.Timedelta(days=1))
        if rpos <= 0 or rpos >= len(idx):
            continue
        r_date = idx[rpos]
        # Stale-event guard: if the first bar on/after the broadcast is more
        # than 5 calendar days out, the symbol wasn't actively trading.
        if (r_date - bcast_date).days > 5:
            continue
        if not registry.contains(symbol, r_date.date()):
            continue
        if rpos + max(HORIZONS) >= len(idx):
            continue
        if r_date not in nifty_close.index:
            continue

        npos = nifty_close.index.get_loc(r_date)
        if npos == 0 or npos + max(HORIZONS) >= len(nifty_close):
            continue

        reaction = (close.iloc[rpos] / close.iloc[rpos - 1] - 1) - \
                   (nifty_close.iloc[npos] / nifty_close.iloc[npos - 1] - 1)
        rec = {"symbol": symbol, "date": str(r_date.date()),
               "quarter": f"{r_date.year}Q{(r_date.month - 1) // 3 + 1}",
               "reaction": float(reaction)}
        for h in HORIZONS:
            stock_ret = close.iloc[rpos + h] / close.iloc[rpos] - 1
            mkt_ret = nifty_close.iloc[npos + h] / nifty_close.iloc[npos] - 1
            rec[f"ar{h}"] = float(stock_ret - mkt_ret)
        out.append(rec)
    return out


def _summarize(events: pd.DataFrame) -> dict:
    if len(events) < 50:
        return {"n_events": int(len(events)), "note": "insufficient"}
    out = {"n_events": int(len(events)),
           "mean_reaction_bp": round(float(events["reaction"].mean() * 1e4), 1)}
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
    parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    conn = get_connection()
    try:
        registry = MembershipRegistry.from_turso(conn)
    finally:
        conn.close()

    events = load_events()
    logger.info("Deduped earnings events: %d across %d symbols",
                len(events), events["symbol"].nunique())

    nifty = load_price_history("^NSEI", limit=None)
    nifty_close = nifty["close"].astype(float)

    universe = set(registry.all_symbols())
    symbols = sorted(set(events["symbol"]) & universe)
    logger.info("Symbols in PIT universe with filings: %d", len(symbols))

    records = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_symbol_events, s, events[events["symbol"] == s],
                        nifty_close, registry): s
            for s in symbols
        }
        for fut in as_completed(futures):
            try:
                records.extend(fut.result())
            except Exception as exc:
                logger.warning("symbol %s failed: %s", futures[fut], exc)

    ar = pd.DataFrame(records)
    logger.info("Usable PIT events: %d", len(ar))

    # Surprise terciles per calendar quarter (cross-sectional, balanced)
    ar["tercile"] = (
        ar.groupby("quarter")["reaction"]
          .transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=["T1_neg", "T2_mid", "T3_pos"]))
    )

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "pit": True,
        "n_events": int(len(ar)),
        "horizons_days": list(HORIZONS),
        "cohorts": {},
        "ship_bar": "T3_pos: |t_monthly| > 3 AND drift > 40bp. 9 cells reported "
                    "jointly; null expected max |t| ~ 2.5.",
    }
    print(f"\n{'tercile':10s} {'n':>6s}   " + "   ".join(f"{h}d: AR / t / hit" for h in HORIZONS))
    for terc, grp in ar.groupby("tercile", observed=True):
        s = _summarize(grp)
        results["cohorts"][str(terc)] = s
        if f"{HORIZONS[0]}d" in s:
            cells = [f"{s[f'{h}d']['mean_ar_bp']:+7.1f}/{s[f'{h}d']['t_monthly']:+.1f}/{s[f'{h}d']['hit_pct']:.0f}%"
                     for h in HORIZONS]
            print(f"{str(terc):10s} {s['n_events']:>6d}   " + "   ".join(cells))
        else:
            print(f"{str(terc):10s} {s['n_events']:>6d}   insufficient")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
