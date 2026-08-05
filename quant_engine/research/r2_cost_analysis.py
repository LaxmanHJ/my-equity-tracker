"""
R2 net-of-cost analysis — does the passing signal survive Zerodha's charges?

Why
---
The R2 NIFTY500 gate PASSED on 2026-08-05 (ml_regression LS Sharpe 1.487,
DSR 0.971, PBO 0.033). But `cpcv_diagnostic` applies NO transaction costs: it
builds a DAILY-REBALANCED long-short book from fwd_1d with no brokerage, STT,
or spread term. Every Sharpe in that run is GROSS.

That is the decisive omission for this project specifically. Seven of eight
strategies in the 2026-06 research program were real and died on costs;
short-term reversal died on costs at t=6.3. A gross-of-cost gate is a necessary
condition, never a sufficient one.

Zerodha charges (zerodha.com/charges, read 2026-08-05)
-----------------------------------------------------
Round-trip cost as a fraction of traded notional, GST 18% on
(brokerage + SEBI + exchange txn):

  EQUITY DELIVERY (CNC)          brokerage 0
    buy : STT 0.100% + txn 0.00307% + SEBI 0.0001% + stamp 0.015% + GST
    sell: STT 0.100% + txn 0.00307% + SEBI 0.0001%              + GST
    round trip ~= 22.2 bp        <- STT on BOTH sides makes daily churn brutal

  EQUITY FUTURES                 brokerage 0.03% or Rs.20/order, lower
    buy : txn 0.00183% + SEBI 0.0001% + stamp 0.002% + brokerage + GST
    sell: STT 0.050% + txn 0.00183% + SEBI 0.0001%   + brokerage + GST
    round trip ~= 6.6 bp  (Rs.20 cap binding, large orders)
                ~= 12.7 bp (0.03% binding, small orders)

Instrument choice is forced, not chosen: India does not permit overnight equity
shorts, so a market-neutral book rebalanced daily must use single-stock futures
on the short leg. Modelling both legs as futures is the CHEAPEST admissible
route, so it is the most generous assumption available — if the edge dies here,
it dies everywhere.

SPREAD IS NOT INCLUDED in the brokerage stack and is charged on top. For midcap
SSF the bid-ask is commonly 5-20 bp. The scenarios below therefore sweep a
spread term separately; the honest read is the mid-to-high end, not the floor.

What this measures
------------------
Same panel, same CPCV splits, same PIT NIFTY500 universe as the gate. The only
change is that per-day turnover is measured and charged. Turnover is the whole
story for a daily-rebalanced book.

Usage:  python3 -m quant_engine.research.r2_cost_analysis
Output: data/r2_cost_analysis.json + console table.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from quant_engine.ml.cpcv_diagnostic import run_cpcv_diagnostic  # noqa: E402
from quant_engine.data.membership import MembershipRegistry  # noqa: E402
from quant_engine.data.turso_client import connect  # noqa: E402

logger = logging.getLogger(__name__)

GST = 0.18
OUT_PATH = PROJECT_ROOT / "data" / "r2_cost_analysis.json"


def futures_round_trip(brokerage_pct: float) -> float:
    """Zerodha equity-futures round-trip cost as a fraction of notional."""
    txn, sebi, stamp, stt = 0.0000183, 0.000001, 0.00002, 0.0005
    buy = brokerage_pct + txn + sebi + stamp + GST * (brokerage_pct + txn + sebi)
    sell = brokerage_pct + stt + txn + sebi + GST * (brokerage_pct + txn + sebi)
    return buy + sell


def delivery_round_trip() -> float:
    """Zerodha equity-delivery (CNC) round-trip cost. Brokerage is zero."""
    txn, sebi, stamp, stt = 0.0000307, 0.000001, 0.00015, 0.001
    buy = stt + txn + sebi + stamp + GST * (txn + sebi)
    sell = stt + txn + sebi + GST * (txn + sebi)
    return buy + sell


# (label, round-trip cost) — spread added on top where noted.
def build_scenarios() -> list[tuple[str, float]]:
    fut_cap = futures_round_trip(0.0002)    # Rs.20 on a ~Rs.1L order
    fut_pct = futures_round_trip(0.0003)    # 0.03% brokerage binding
    return [
        ("gross (no costs)",                     0.0),
        ("futures, Rs.20 cap, no spread",        fut_cap),
        ("futures, 0.03% brk, no spread",        fut_pct),
        ("futures + 5bp spread",                 fut_cap + 0.0005),
        ("futures + 10bp spread",                fut_cap + 0.0010),
        ("futures + 20bp spread",                fut_cap + 0.0020),
        ("equity delivery (CNC), no spread",     delivery_round_trip()),
    ]


def run_study() -> dict:
    with connect() as conn:
        registry = MembershipRegistry.from_turso(conn, "NIFTY500")

    scenarios = build_scenarios()
    # ONE CPCV pass; costs are applied to the same gross series + measured
    # turnover. Re-running the panel per scenario would cost ~30min each.
    res = run_cpcv_diagnostic(
        pit_universe=registry, pit_index_name="NIFTY500",
        n_groups=6, n_test_groups=2, n_trials=100,
        cost_sweep=[c for _, c in scenarios],
    )
    by_cost = {round(e["round_trip"], 10): e for e in res["cost_sweep"]}

    rows = []
    for label, cost in scenarios:
        e = by_cost[round(cost, 10)]
        t = e["tracks"]["ml_regression"]
        pbo = e.get("pbo")
        rows.append({
            "scenario": label,
            "round_trip_bps": e["round_trip_bps"],
            "lo_sharpe": t["lo_sharpe"], "ls_sharpe": t["ls_sharpe"],
            "dsr_lo": t["dsr_lo"], "dsr_ls": t["dsr_ls"], "pbo": pbo,
            "passes_gate": bool(((t["dsr_lo"] or 0) > 0.95 or (t["dsr_ls"] or 0) > 0.95)
                                and pbo is not None and pbo < 0.15),
        })

    turnover = res.get("mean_daily_turnover_ls", {}).get("ml_regression")
    out = {
        "study": "R2 net-of-cost analysis (Zerodha)",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "charges_source": "zerodha.com/charges, read 2026-08-05",
        "mean_daily_turnover_ls": turnover,
        "n_samples_total": res["n_samples_total"],
        "note": ("Both legs modelled as equity futures - the cheapest admissible route, "
                 "since India forbids overnight equity shorts. Spread is charged on top "
                 "of the brokerage stack and is NOT in the no-spread rows."),
        "scenarios": rows,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))

    print("\n=== R2 net-of-cost (ml_regression, NIFTY500 PIT) ===")
    if turnover:
        print(f"mean daily turnover, both legs: {turnover:.1%} of book per day")
    print(f"\n{'scenario':<34}{'rt bp':>7}{'LO':>8}{'LS':>8}{'DSR_LS':>9}{'PBO':>8}  gate")
    for r in rows:
        f = lambda x: float('nan') if x is None else x  # noqa: E731
        print(f"{r['scenario']:<34}{r['round_trip_bps']:>7.1f}{f(r['lo_sharpe']):>8.2f}"
              f"{f(r['ls_sharpe']):>8.2f}{f(r['dsr_ls']):>9.3f}{f(r['pbo']):>8.3f}"
              f"  {'PASS' if r['passes_gate'] else 'fail'}")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    run_study()
