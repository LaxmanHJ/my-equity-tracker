"""
CPCV + DSR/PBO diagnostic runner — the ship/no-ship gate (P2-a + P2-b applied).

This is the decisive statistic for putting live capital behind any track. The
walk-forward diagnostic (`diagnostic.py`) gives ONE IC path per track. This
runner re-evaluates the same three tracks (ml, linear, ml_regression) under
Combinatorial Purged CV — producing a *distribution* of out-of-sample Sharpes
per track — then deflates the best with the Deflated Sharpe Ratio and measures
Probability of Backtest Overfitting across tracks.

Why this exists
---------------
After the post-PIT diagnostic (IC ~0.009-0.012, ICIR ~0.09) and the finding
that the meta-labeler adds ~0 on the honest universe, the only open question
is: *is there any shippable edge at all?* CPCV + DSR answers it honestly:

  • CPCV  → robust Sharpe distribution, not a single lucky path
  • DSR   → P(true Sharpe > selection-bias floor) given N trials
  • PBO   → fraction of IS/OOS splits where the IS-best track is below OOS median

Long-only vs long-short
-----------------------
The post-PIT diagnostic surfaced that the linear composite's high-conviction
BUYs win only ~34% on the honest universe — evidence the signal may be loaded
on the SHORT side. So this runner computes TWO strategy Sharpes per path:

  • long_only  — equal-weight top-quantile by score, daily rebalance (what
                 production actually trades)
  • long_short — top-quantile minus bottom-quantile (what a market-neutral
                 book would capture; isolates the cross-sectional alpha)

If long_short Sharpe ≫ long_only Sharpe, the edge is real but only
harvestable market-neutral — a structural argument for P3-d, not a reason
to ship the current long-only path.

Cost
----
CPCV trains an RF per (combination × track). With n_groups=6, n_test_groups=2
that's C(6,2)=15 combinations × 2 RF fits = 30 fits over the full PIT panel.
Budget ~30-60 min wall-clock. Increase n_groups for a finer distribution at
super-linear cost.

Run
---
    # Honest universe (recommended — this is the ship/no-ship number):
    python -m quant_engine.ml.cpcv_diagnostic --pit

    # Legacy universe for comparison:
    python -m quant_engine.ml.cpcv_diagnostic

    # Finer distribution (slower):
    python -m quant_engine.ml.cpcv_diagnostic --pit --n-groups 8 --n-test-groups 2
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from quant_engine.config import PROJECT_ROOT
from quant_engine.ml.cpcv import cpcv_splits
from quant_engine.ml.validation import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from quant_engine.ml.diagnostic import (
    LABEL_HORIZON_DAYS,
    MIN_TRAIN_AFTER_PURGE,
    _build_cs_rank_label,
    _build_regression_pipeline,
    _load_deployed_rf_params,
    build_dataset_with_horizons,
)
from quant_engine.ml.trainer import RF_PARAMS, _build_pipeline

logger = logging.getLogger(__name__)

TRACKS = ("ml", "linear", "ml_regression")
CPCV_OUTPUT_PATH = PROJECT_ROOT / "data" / "cpcv_diagnostic.json"

# Trading-days per year for Sharpe annualization.
TRADING_DAYS = 252

# Quantile for the long/short legs (top/bottom 20%).
LEG_QUANTILE = 0.20

# Minimum cross-section per date for a daily portfolio return to be meaningful.
MIN_XS_FOR_PORTFOLIO = 5

# Default audit-trial count for DSR selection-bias deflation. This is the
# honest count of distinct OOS strategy evaluations made across the whole ML
# audit (tracks × horizons × thresholds × universes × meta-labeler configs).
# Documented in wiki/concepts/ml_audit_2026_05_21.md §3. Override with
# --n-trials once you have an exact count.
DEFAULT_N_TRIALS = 100


# ── Per-path strategy returns ───────────────────────────────────────────────
def _daily_portfolio_returns(
    score: np.ndarray,
    fwd_1d: np.ndarray,
    dates: pd.DatetimeIndex,
    quantile: float = LEG_QUANTILE,
) -> tuple[pd.Series, pd.Series]:
    """Build daily long-only and long-short return series from a signed score.

    For each date with ≥ MIN_XS_FOR_PORTFOLIO valid names:
      • long_only  = mean fwd_1d of the top-`quantile` by score
      • long_short = mean(top) − mean(bottom-`quantile`)

    Using fwd_1d (next-day return) gives a daily-rebalanced series whose Sharpe
    is annualizable. NaN scores / returns are dropped per date.

    Returns (long_only_series, long_short_series) indexed by date.
    """
    df = pd.DataFrame({"score": score, "fwd": fwd_1d}, index=dates)
    df = df.dropna(subset=["score", "fwd"])
    lo_rows: dict[pd.Timestamp, float] = {}
    ls_rows: dict[pd.Timestamp, float] = {}
    for d, grp in df.groupby(level=0):
        if len(grp) < MIN_XS_FOR_PORTFOLIO:
            continue
        n_leg = max(1, int(round(len(grp) * quantile)))
        ordered = grp.sort_values("score")
        bottom = ordered.head(n_leg)["fwd"].mean()
        top = ordered.tail(n_leg)["fwd"].mean()
        lo_rows[d] = float(top)
        ls_rows[d] = float(top - bottom)
    lo = pd.Series(lo_rows).sort_index()
    ls = pd.Series(ls_rows).sort_index()
    return lo, ls


def _annualized_sharpe(returns: pd.Series) -> float:
    """Annualized Sharpe of a daily return series. NaN if degenerate."""
    r = returns.dropna()
    if len(r) < 20 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS))


# ── Per-combination track evaluation ────────────────────────────────────────
def _score_tracks_on_split(
    X: pd.DataFrame,
    meta: pd.DataFrame,
    y: np.ndarray,
    cs_rank_label: pd.Series,
    dates: pd.DatetimeIndex,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    rf_params_clf: dict,
    rf_params_reg: dict,
) -> dict[str, np.ndarray]:
    """Train each track on train_idx, return per-track signed score on test_idx.

    Mirrors diagnostic.py's per-fold logic exactly so the CPCV numbers are
    comparable to the walk-forward ones. NaN scores where a track can't fit.
    """
    n_test = len(test_idx)
    out: dict[str, np.ndarray] = {t: np.full(n_test, np.nan) for t in TRACKS}

    if len(train_idx) < MIN_TRAIN_AFTER_PURGE:
        return out

    X_train = X.iloc[train_idx]
    X_test = X.iloc[test_idx]

    # ml — 3-class RF classifier, signed score = P(BUY) − P(SELL)
    y_train = y[train_idx]
    try:
        clf = _build_pipeline(rf_params_clf)
        clf.fit(X_train, y_train)
        classes = list(clf.named_steps["rf"].classes_)
        if 1 in classes and -1 in classes:
            proba = clf.predict_proba(X_test)
            bi, si = classes.index(1), classes.index(-1)
            out["ml"] = proba[:, bi] - proba[:, si]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml track fit failed: %s", exc)

    # linear — deterministic composite already in meta
    out["linear"] = meta.iloc[test_idx]["linear_score"].astype(float).values

    # ml_regression — RF regressor on cross-sectional rank label
    y_reg = cs_rank_label.iloc[train_idx].values
    reg_mask = ~np.isnan(y_reg)
    if reg_mask.sum() >= MIN_TRAIN_AFTER_PURGE:
        try:
            reg = _build_regression_pipeline(rf_params_reg)
            reg.fit(X_train.iloc[reg_mask], y_reg[reg_mask])
            out["ml_regression"] = np.clip(reg.predict(X_test), -1.0, 1.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ml_regression track fit failed: %s", exc)

    return out


# ── Main runner ─────────────────────────────────────────────────────────────
def run_cpcv_diagnostic(
    pit_universe=None,
    pit_index_name: str = "NIFTY200",
    n_groups: int = 6,
    n_test_groups: int = 2,
    n_trials: int = DEFAULT_N_TRIALS,
) -> dict:
    """Run CPCV across the three tracks, deflate with DSR, measure PBO.

    Returns a dict (also written to CPCV_OUTPUT_PATH) with, per track:
      • long_only / long_short Sharpe distribution across combinations
      • mean, std, median, q05, q95 of each
      • DSR of the pooled Sharpe given n_trials
    Plus a cross-track PBO using per-combination long-short Sharpes.
    """
    logger.info(
        "Building CPCV dataset … (PIT %s)",
        "ENABLED" if pit_universe is not None else "disabled",
    )
    X, meta = build_dataset_with_horizons(
        pit_universe=pit_universe, pit_index_name=pit_index_name,
    )
    dates = pd.DatetimeIndex(X.index)
    y = meta["label"].values
    cs_rank_label = _build_cs_rank_label(meta)

    rf_params_clf, src = _load_deployed_rf_params(RF_PARAMS)
    rf_params_reg = {k: v for k, v in rf_params_clf.items() if k != "class_weight"}
    logger.info("RF params source=%s", src)

    splits = list(cpcv_splits(
        dates, n_groups=n_groups, n_test_groups=n_test_groups,
        label_horizon=LABEL_HORIZON_DAYS,
    ))
    logger.info(
        "CPCV: %d combinations (n_groups=%d, n_test_groups=%d)",
        len(splits), n_groups, n_test_groups,
    )

    # Per-track per-combination Sharpes.
    lo_sharpes: dict[str, list[float]] = {t: [] for t in TRACKS}
    ls_sharpes: dict[str, list[float]] = {t: [] for t in TRACKS}
    # For PBO: per-combination long-short daily returns, aligned per track.
    ls_returns_by_combo: list[dict[str, pd.Series]] = []

    for i, split in enumerate(splits, 1):
        logger.info(
            "Combination %d/%d: test groups %s (train=%d, test=%d)",
            i, len(splits), split.test_group_ids,
            len(split.train_idx), len(split.test_idx),
        )
        scores = _score_tracks_on_split(
            X, meta, y, cs_rank_label, dates,
            split.train_idx, split.test_idx,
            rf_params_clf, rf_params_reg,
        )
        test_dates = dates[split.test_idx]
        fwd_1d = meta.iloc[split.test_idx]["fwd_ret_1d"].values
        combo_ls_returns: dict[str, pd.Series] = {}
        for track in TRACKS:
            lo, ls = _daily_portfolio_returns(scores[track], fwd_1d, test_dates)
            lo_sharpes[track].append(_annualized_sharpe(lo))
            ls_sharpes[track].append(_annualized_sharpe(ls))
            combo_ls_returns[track] = ls
        ls_returns_by_combo.append(combo_ls_returns)

    # ── Per-track summary + DSR ──────────────────────────────────────────────
    def _summary(vals: list[float]) -> dict:
        a = np.array([v for v in vals if not np.isnan(v)], dtype=float)
        if a.size == 0:
            return {"n": 0, "mean": None, "std": None, "median": None,
                    "q05": None, "q95": None}
        return {
            "n": int(a.size),
            "mean": round(float(a.mean()), 4),
            "std": round(float(a.std(ddof=1)), 4) if a.size > 1 else None,
            "median": round(float(np.median(a)), 4),
            "q05": round(float(np.quantile(a, 0.05)), 4),
            "q95": round(float(np.quantile(a, 0.95)), 4),
        }

    track_results: dict[str, dict] = {}
    for track in TRACKS:
        lo_sum = _summary(lo_sharpes[track])
        ls_sum = _summary(ls_sharpes[track])
        # DSR on the long-short pooled mean Sharpe — the market-neutral edge is
        # the honest alpha question. trial_sharpes = this track's per-combination
        # long-short Sharpes (their dispersion sets the PSR standard error);
        # n_trials sets the selection-bias benchmark.
        ls_clean = [v for v in ls_sharpes[track] if not np.isnan(v)]
        lo_clean = [v for v in lo_sharpes[track] if not np.isnan(v)]
        dsr_ls = (
            deflated_sharpe_ratio(
                observed_sharpe=float(np.mean(ls_clean)),
                trial_sharpes=ls_clean,
                n_samples=n_trials,
            ) if len(ls_clean) >= 2 else None
        )
        dsr_lo = (
            deflated_sharpe_ratio(
                observed_sharpe=float(np.mean(lo_clean)),
                trial_sharpes=lo_clean,
                n_samples=n_trials,
            ) if len(lo_clean) >= 2 else None
        )
        track_results[track] = {
            "long_only_sharpe": lo_sum,
            "long_short_sharpe": ls_sum,
            "dsr_long_only": round(dsr_lo, 4) if dsr_lo is not None else None,
            "dsr_long_short": round(dsr_ls, 4) if dsr_ls is not None else None,
        }

    # ── Cross-track PBO on long-short returns ────────────────────────────────
    # Build a (period × track) returns matrix by concatenating each track's
    # per-combination long-short returns onto a shared date axis (mean across
    # combinations that cover the same date). PBO then asks: is the IS-best
    # track below OOS median? Near 0.5 ⇒ no track reliably dominates.
    pbo_value: Optional[float] = None
    pbo_note = ""
    try:
        per_track_series: dict[str, pd.Series] = {}
        for track in TRACKS:
            frames = [c[track] for c in ls_returns_by_combo if len(c[track]) > 0]
            if frames:
                # Average across combinations covering the same date.
                concat = pd.concat(frames, axis=1)
                per_track_series[track] = concat.mean(axis=1)
        if len(per_track_series) >= 2:
            mat = pd.DataFrame(per_track_series).dropna()
            if len(mat) >= 16 and mat.shape[1] >= 2:
                pbo_value = float(probability_of_backtest_overfitting(
                    mat.values, n_slices=min(16, (len(mat) // 2) * 2),
                ))
            else:
                pbo_note = f"insufficient aligned periods ({len(mat)}) for PBO"
        else:
            pbo_note = "fewer than 2 tracks produced returns"
    except Exception as exc:  # noqa: BLE001
        pbo_note = f"PBO computation failed: {exc}"

    result = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "pit_universe_active": pit_universe is not None,
        "pit_index_name": pit_index_name if pit_universe is not None else None,
        "n_groups": n_groups,
        "n_test_groups": n_test_groups,
        "n_combinations": len(splits),
        "n_samples_total": int(len(X)),
        "n_trials_for_dsr": n_trials,
        "rf_params_source": src,
        "leg_quantile": LEG_QUANTILE,
        "tracks": track_results,
        "pbo_long_short": pbo_value,
        "pbo_note": pbo_note,
        "notes": (
            "CPCV (AFML Ch.12) over 3 tracks; per-combination annualized Sharpe "
            "for long-only (top-quantile) and long-short (top minus bottom "
            "quantile) daily-rebalanced portfolios. DSR (AFML Ch.14) deflates "
            "the pooled mean Sharpe by n_trials_for_dsr selection-bias trials. "
            "PBO across tracks on long-short returns. DSR > 0.95 AND PBO < 0.15 "
            "is the ship gate. long_short >> long_only Sharpe ⇒ edge is "
            "market-neutral-only (argues for P3-d, not the current long-only path)."
        ),
    }

    CPCV_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CPCV_OUTPUT_PATH, "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    logger.info("CPCV diagnostic written to %s", CPCV_OUTPUT_PATH)
    return result


def _print_report(r: dict) -> None:
    print("\n=== CPCV + DSR/PBO Diagnostic ===")
    print(
        f"PIT={'ON' if r['pit_universe_active'] else 'off'}  "
        f"combinations={r['n_combinations']}  "
        f"samples={r['n_samples_total']}  n_trials(DSR)={r['n_trials_for_dsr']}"
    )
    print(f"\n{'track':<15}{'LO Sharpe':>12}{'LS Sharpe':>12}{'DSR(LO)':>10}{'DSR(LS)':>10}")
    for track, tr in r["tracks"].items():
        lo = tr["long_only_sharpe"]["mean"]
        ls = tr["long_short_sharpe"]["mean"]
        print(
            f"{track:<15}"
            f"{(lo if lo is not None else float('nan')):>12.3f}"
            f"{(ls if ls is not None else float('nan')):>12.3f}"
            f"{(tr['dsr_long_only'] if tr['dsr_long_only'] is not None else float('nan')):>10.3f}"
            f"{(tr['dsr_long_short'] if tr['dsr_long_short'] is not None else float('nan')):>10.3f}"
        )
    print(f"\nPBO (long-short, across tracks): {r['pbo_long_short']}  {r['pbo_note']}")
    print(
        "\nShip gate: DSR > 0.95 AND PBO < 0.15. "
        "LS >> LO Sharpe ⇒ edge is market-neutral-only."
    )


if __name__ == "__main__":
    import argparse
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pit", action="store_true",
                     help="Apply point-in-time membership filter (the honest universe).")
    cli.add_argument("--pit-index", type=str, default="NIFTY200")
    cli.add_argument("--n-groups", type=int, default=6,
                     help="CPCV time groups N (default 6). More = finer distribution, slower.")
    cli.add_argument("--n-test-groups", type=int, default=2,
                     help="CPCV test groups k (default 2).")
    cli.add_argument("--n-trials", type=int, default=DEFAULT_N_TRIALS,
                     help=f"Selection-bias trial count for DSR (default {DEFAULT_N_TRIALS}).")
    args = cli.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    pit_registry = None
    if args.pit:
        from quant_engine.data.membership import MembershipRegistry
        from quant_engine.data.turso_client import connect as _t_connect
        with _t_connect() as _conn:
            pit_registry = MembershipRegistry.from_turso(_conn, args.pit_index)
        logger.info(
            "PIT registry: %d members ever",
            len(pit_registry.all_symbols(args.pit_index)),
        )

    result = run_cpcv_diagnostic(
        pit_universe=pit_registry,
        pit_index_name=args.pit_index,
        n_groups=args.n_groups,
        n_test_groups=args.n_test_groups,
        n_trials=args.n_trials,
    )
    _print_report(result)
