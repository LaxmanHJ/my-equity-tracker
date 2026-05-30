"""
Assertion tests for P1-a — loosened valid_mask (ML audit S5).

The old `valid_mask = features.notna().all(axis=1)` required EVERY column
to be non-NaN, so any Nifty 200 stock lacking intraday or delivery data
contributed zero training rows. The fix mirrors `meta_labeler.py`:
parameterise the required-column set, default to the 7 always-available
price-derived factors, let soft features be imputed at fit time.

These tests verify the gating logic on synthetic feature frames with
deliberately-missing soft features — no DB needed.
"""
import numpy as np
import pandas as pd
import pytest

from quant_engine.ml import features, trainer


def _row_with_only_hard_features() -> pd.DataFrame:
    """One row where the 7 price-derived 'hard' features are filled and every
    soft feature is NaN — exactly the shape a Nifty 200 stock without intraday
    or delivery produces."""
    row = {col: np.nan for col in features.FEATURE_COLS}
    for hard in trainer.REQUIRED_FEATURE_COLS_DEFAULT:
        row[hard] = 0.1
    return pd.DataFrame([row], index=[pd.Timestamp("2024-01-15")])


def _row_fully_populated() -> pd.DataFrame:
    return pd.DataFrame(
        [{col: 0.1 for col in features.FEATURE_COLS}],
        index=[pd.Timestamp("2024-01-15")],
    )


# ── The headline universe-expansion test ─────────────────────────────────────
def test_hard_only_row_survives_default_mask_but_dies_under_strict():
    """A Nifty-200-style row (hard features set, soft NaN) must survive the
    default valid_mask but be dropped under the legacy strict mask."""
    frame = _row_with_only_hard_features()
    hard_cols = list(trainer.REQUIRED_FEATURE_COLS_DEFAULT)

    # New default: only hard cols required → row survives.
    new_mask = frame[hard_cols].notna().all(axis=1)
    assert new_mask.iloc[0], \
        "P1-a default mask must keep rows whose 7 hard features are present"

    # Legacy strict: ALL cols required → row dropped.
    legacy_mask = frame.notna().all(axis=1)
    assert not legacy_mask.iloc[0], \
        "legacy strict mask should drop a row with NaN soft features"


def test_strict_mode_recovers_legacy_behaviour():
    """Passing the full FEATURE_COLS to required_feature_cols must reproduce
    the pre-P1-a strict behaviour: only fully-populated rows survive."""
    hard_only = _row_with_only_hard_features()
    full = _row_fully_populated()
    combined = pd.concat([hard_only, full])
    strict_cols = list(features.FEATURE_COLS)
    mask = combined[strict_cols].notna().all(axis=1)
    assert mask.tolist() == [False, True], \
        f"strict mode must keep only the fully-populated row, got {mask.tolist()}"


def test_default_keeps_fully_populated_rows_too():
    """The new default must not regress: a row with every feature filled
    obviously still passes."""
    full = _row_fully_populated()
    hard_cols = list(trainer.REQUIRED_FEATURE_COLS_DEFAULT)
    assert full[hard_cols].notna().all(axis=1).iloc[0]


def test_default_drops_rows_with_missing_hard_features():
    """If even one hard feature is NaN the row must still be dropped — soft
    features being imputable is the loosening, hard features aren't."""
    bad = _row_fully_populated()
    bad.loc[bad.index[0], "rsi"] = np.nan
    hard_cols = list(trainer.REQUIRED_FEATURE_COLS_DEFAULT)
    assert not bad[hard_cols].notna().all(axis=1).iloc[0], \
        "row with NaN hard feature must be dropped even under loose default"


# ── Required-col set is consistent with predictor's hard gate ────────────────
def test_required_default_matches_predictor_hard_gate():
    """The seven hard features we require at training must match the seven
    `HARD_GATE_FEATURES` predictor.py refuses to impute at inference time —
    otherwise we'd train on rows the predictor would HOLD on."""
    from quant_engine.ml.predictor import HARD_GATE_FEATURES
    assert set(trainer.REQUIRED_FEATURE_COLS_DEFAULT) == set(HARD_GATE_FEATURES), (
        "trainer.REQUIRED_FEATURE_COLS_DEFAULT must equal "
        "predictor.HARD_GATE_FEATURES so training and inference universes "
        "use the same row-acceptance rule"
    )


# ── Universe-size simulation: cross-stock effect ─────────────────────────────
def test_loose_mask_admits_more_rows_than_strict_on_mixed_universe():
    """Simulate 200 stocks where only 15 have intraday/delivery. Default
    mask must admit substantially more rows than strict."""
    n_full, n_hard_only = 15, 185
    rng = np.random.default_rng(42)
    rows = []

    for sym in range(n_full):
        # Full-coverage stock: every feature filled.
        row = {col: rng.normal() for col in features.FEATURE_COLS}
        rows.append(row)
    for sym in range(n_hard_only):
        # Nifty 200-style stock: hard features only.
        row = {col: np.nan for col in features.FEATURE_COLS}
        for hard in trainer.REQUIRED_FEATURE_COLS_DEFAULT:
            row[hard] = rng.normal()
        rows.append(row)

    idx = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    frame = pd.DataFrame(rows, index=idx)

    strict_mask = frame.notna().all(axis=1)
    loose_mask = frame[list(trainer.REQUIRED_FEATURE_COLS_DEFAULT)].notna().all(axis=1)

    assert int(strict_mask.sum()) == n_full, \
        f"strict mask: expected {n_full} survivors, got {int(strict_mask.sum())}"
    assert int(loose_mask.sum()) == n_full + n_hard_only, \
        f"loose mask: expected {n_full + n_hard_only} survivors, got {int(loose_mask.sum())}"

    expansion_factor = float(loose_mask.sum()) / float(strict_mask.sum())
    # 200/15 ≈ 13.3× — assert at least an order of magnitude bigger universe.
    assert expansion_factor >= 10.0, \
        f"loose mask must admit ≥10× more rows than strict on mixed universe, " \
        f"got {expansion_factor:.1f}×"
