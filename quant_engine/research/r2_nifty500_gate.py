"""
R2 — NIFTY500 PIT universe expansion against the C0 ship gate.

PRE-REGISTERED DECLARED RULE (fixed before the first run, 2026-08-03)
====================================================================

Motivation
----------
The 2026-06-04 audit closed with no track clearing the C0 gate on the honest
point-in-time universe: DSR = 0.000 for all three tracks (`ml`, `linear`,
`ml_regression`) and PBO = 0.585. R1 (alternative horizons 1d/3d) was run and
FAILED, closed 2026-06-10. R2 is the last research item that could plausibly
change that verdict without inventing a new signal: the hypothesis is that the
edge is real but the standard error is too wide at N≈340 symbols, and that
~2.5x the cross-section shrinks SE enough for an existing ~0.6 LS Sharpe to
clear DSR.

If that hypothesis is wrong, the honest conclusion is that the feature set has
no shippable edge at any universe size, and the prediction track is closed.

Design
------
Universe    : NIFTY500 point-in-time membership, 940 spells / 837 ever-members,
              reconstructed from NSE Indices press releases
              (quant_engine/data/scrape_niftyindices.py), 2018-01-01 onward.
Panel       : quant_engine.ml.diagnostic.build_dataset_with_horizons, cadence-
              masked, PIT row filter on NIFTY500.
Method      : CPCV (AFML Ch.12), n_groups=6, n_test_groups=2 => 15 combinations,
              20-day label horizon doubling as the purge, per-combination
              annualized Sharpe for long-only (top quintile) and long-short
              (top minus bottom quintile) daily-rebalanced portfolios.
Deflation   : DSR (AFML Ch.14) with n_trials=100 — the same conservative audit
              count used for every prior run, so the comparison is like-for-like.
Comparator  : data/cpcv_diagnostic_nifty200_pit.json (post-cadence-mask NIFTY200
              PIT, n=392,822, best DSR 0.160 ml_regression LO, PBO 0.624).
              Feature code is held CONSTANT across the two runs; that is the
              whole reason the cadence mask landed before either of them.

GATES (both required for PASS, on at least one track)
-----------------------------------------------------
G1. DSR > 0.95 on either the long-only or long-short leg.
G2. PBO < 0.15 (long-short, across tracks).

This is the standing C0 gate from wiki/live_trading_checklist.md, unchanged.
The bar to beat from the audit is post-PIT ml_regression LS Sharpe 0.595 at
DSR 0.000.

FAIL branch: R2 is closed. No variants, no re-runs, no threshold shopping, no
"try n_trials=50". One run. A FAIL is a terminal finding about the feature set,
not an invitation to widen the search — and per the audit's own reasoning,
lowering n_trials to rescue DSR would be exactly the selection bias DSR exists
to penalize.

Known limitation, declared up front
-----------------------------------
The NIFTY500 membership reconstruction does NOT exactly reproduce the
independently hand-built NIFTY200 rows: ~4.5% symbol-date disagreement,
concentrated before 2022 (32 disagreements at 2018 probe dates falling to 2 by
2024; 2025 clean). Causes are unparsed events, symbol renames the method cannot
see, and DUMMYVEDL1-4 junk in the stored comparison data.

**The full 2018+ range is used anyway, deliberately.** Membership error
mislabels which rows are masked; that is noise, and noise attenuates measured
Sharpe. It therefore biases this run AGAINST passing, which is the correct
direction for a ship gate — a gate must never be biased toward the result that
deploys capital. It also keeps the date range identical to the NIFTY200
comparator. If the run PASSES, the pre-2022 gap must be closed and the run
repeated before any capital moves; if it FAILS, the gap cannot be the reason.

Usage:  python3 -m quant_engine.research.r2_nifty500_gate
Output: data/r2_nifty500_gate.json + console summary.
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

INDEX_NAME = "NIFTY500"
N_GROUPS = 6
N_TEST_GROUPS = 2
N_TRIALS = 100

DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.15

OUT_PATH = PROJECT_ROOT / "data" / "r2_nifty500_gate.json"
BASELINE_PATH = PROJECT_ROOT / "data" / "cpcv_diagnostic_nifty200_pit.json"


def run_study() -> dict:
    with connect() as conn:
        registry = MembershipRegistry.from_turso(conn, INDEX_NAME)
    logger.info("PIT registry %s: %d ever-members",
                INDEX_NAME, len(registry.all_symbols(INDEX_NAME)))

    diag = run_cpcv_diagnostic(
        pit_universe=registry,
        pit_index_name=INDEX_NAME,
        n_groups=N_GROUPS,
        n_test_groups=N_TEST_GROUPS,
        n_trials=N_TRIALS,
    )

    # G1: best DSR across tracks and legs.
    best_dsr, best_where = 0.0, None
    for track, tr in diag["tracks"].items():
        for leg in ("dsr_long_only", "dsr_long_short"):
            v = tr.get(leg)
            if v is not None and v > best_dsr:
                best_dsr, best_where = float(v), f"{track}.{leg}"

    pbo = diag.get("pbo_long_short")
    g1 = best_dsr > DSR_THRESHOLD
    g2 = pbo is not None and pbo < PBO_THRESHOLD

    baseline = {}
    if BASELINE_PATH.exists():
        b = json.loads(BASELINE_PATH.read_text())
        baseline = {
            "n_samples_total": b.get("n_samples_total"),
            "pbo_long_short": b.get("pbo_long_short"),
            "tracks": {k: {"lo": v["long_only_sharpe"]["mean"],
                           "ls": v["long_short_sharpe"]["mean"],
                           "dsr_lo": v["dsr_long_only"],
                           "dsr_ls": v["dsr_long_short"]}
                       for k, v in b.get("tracks", {}).items()},
        }

    results = {
        "study": "R2 — NIFTY500 PIT universe expansion",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "declared_rule": "see module docstring — fixed before first run",
        "index_name": INDEX_NAME,
        "n_ever_members": len(registry.all_symbols(INDEX_NAME)),
        "n_samples_total": diag["n_samples_total"],
        "n_combinations": diag["n_combinations"],
        "n_trials_for_dsr": diag["n_trials_for_dsr"],
        "pit_universe_active": diag["pit_universe_active"],
        "tracks": {k: {"lo": v["long_only_sharpe"]["mean"],
                       "ls": v["long_short_sharpe"]["mean"],
                       "dsr_lo": v["dsr_long_only"],
                       "dsr_ls": v["dsr_long_short"]}
                   for k, v in diag["tracks"].items()},
        "pbo_long_short": pbo,
        "best_dsr": best_dsr,
        "best_dsr_where": best_where,
        "nifty200_baseline": baseline,
        "gates": {
            "G1_dsr_gt_0.95": g1,
            "G2_pbo_lt_0.15": g2,
            "verdict": "PASS" if (g1 and g2) else "FAIL",
        },
    }

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))

    print("\n=== R2 — NIFTY500 PIT gate ===")
    print(f"ever-members={results['n_ever_members']}  samples={results['n_samples_total']}  "
          f"combinations={results['n_combinations']}  n_trials={results['n_trials_for_dsr']}")
    if baseline:
        print(f"(NIFTY200 comparator: samples={baseline['n_samples_total']}, "
              f"PBO={baseline['pbo_long_short']:.3f})")
    print(f"\n{'track':<15}{'LO Sharpe':>12}{'LS Sharpe':>12}{'DSR(LO)':>10}{'DSR(LS)':>10}")
    for t, v in results["tracks"].items():
        f = lambda x: float("nan") if x is None else x  # noqa: E731
        print(f"{t:<15}{f(v['lo']):>12.3f}{f(v['ls']):>12.3f}"
              f"{f(v['dsr_lo']):>10.3f}{f(v['dsr_ls']):>10.3f}")
    print(f"\nbest DSR = {best_dsr:.3f} ({best_where})   PBO = {pbo}")
    print(f"G1 DSR>0.95: {g1}    G2 PBO<0.15: {g2}")
    print(f"\nVERDICT: {results['gates']['verdict']}")
    if results["gates"]["verdict"] == "FAIL":
        print("Per the declared rule: R2 is closed. No variants, no re-runs.")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    run_study()
