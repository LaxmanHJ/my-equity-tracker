"""
Assertion tests for the PIT row filter wiring (P2-c follow-up).

The ``apply_pit_row_filter`` helper is the single seam where survivorship
correction is applied per-symbol per-date in the ML pipeline. If it's
wrong, the entire post-PIT diagnostic is wrong. These tests cover:

  • bit-identical behaviour when ``registry=None`` (legacy preservation)
  • a symbol delisted in 2020 has pre-2020 rows kept and post-2020 rows dropped
  • a symbol never in the registry is fully dropped (audit S8 — the
    "wasn't a member of N200" case)
  • an empty input DataFrame round-trips unchanged
  • index_name namespacing: a NIFTY50-only symbol is dropped when checking
    NIFTY200 membership
  • the helper preserves DataFrame column dtype + index identity for kept rows

All synthetic — no DB.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant_engine.data.membership import (
    MembershipInterval,
    MembershipRegistry,
    apply_pit_row_filter,
)


def _synth_features(n_days: int = 250, start: str = "2018-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame({"rsi": np.linspace(-1, 1, n_days), "macd": 0.0}, index=idx)


# ── Legacy preservation ─────────────────────────────────────────────────────
def test_apply_pit_row_filter_is_noop_with_no_registry():
    """Bit-identical preservation: with registry=None the filter is a
    pass-through. This guarantees the post-P2-c diagnostic produces the
    same numbers as today's diagnostic when --pit is not passed."""
    features = _synth_features()
    out = apply_pit_row_filter(features, "RELIANCE", registry=None)
    pd.testing.assert_frame_equal(out, features)


def test_apply_pit_row_filter_handles_empty_input():
    """Edge case: a symbol whose feature build failed and returned an empty
    frame must round-trip without error."""
    empty = pd.DataFrame(columns=["rsi", "macd"], index=pd.DatetimeIndex([]))
    reg = MembershipRegistry([
        MembershipInterval("NIFTY200", "X", date(2020, 1, 1), None),
    ])
    out = apply_pit_row_filter(empty, "X", reg)
    assert len(out) == 0
    assert list(out.columns) == ["rsi", "macd"]


# ── The survivorship fix in action ──────────────────────────────────────────
def test_apply_pit_row_filter_keeps_pre_delist_rows_drops_post():
    """A symbol delisted on 2020-09-25 must keep its 2018-2020-09-25 rows
    and lose its 2020-09-26+ rows — exact DHFL case."""
    features = _synth_features(n_days=1000, start="2018-01-02")  # spans 2018-2021
    reg = MembershipRegistry([
        MembershipInterval("NIFTY200", "DHFL", date(2018, 1, 1), date(2020, 9, 25)),
    ])
    out = apply_pit_row_filter(features, "DHFL", reg)

    kept_dates = pd.DatetimeIndex(out.index).date
    assert all(d <= date(2020, 9, 25) for d in kept_dates), \
        "no post-delist rows must survive"
    assert all(d >= date(2018, 1, 1) for d in kept_dates), \
        "no pre-membership rows must survive (none expected here)"
    # Sanity: the cutoff isn't off by one or empty.
    assert any(d == date(2020, 9, 25) for d in kept_dates), \
        "the delist-day row itself is inclusive (registry.contains is inclusive)"
    assert len(out) < len(features), "some rows must be dropped"


def test_apply_pit_row_filter_drops_unknown_symbol_entirely():
    """The audit S8 case: a symbol present in price_history but NEVER an
    index member must be fully dropped. Today's diagnostic would have
    included its rows; the post-PIT diagnostic must not."""
    features = _synth_features(250)
    reg = MembershipRegistry([
        MembershipInterval("NIFTY200", "OTHER_SYMBOL", date(2018, 1, 1), None),
    ])
    out = apply_pit_row_filter(features, "NEVER_A_MEMBER", reg)
    assert len(out) == 0, \
        "a never-member symbol must contribute zero rows after PIT filter"


def test_apply_pit_row_filter_respects_index_namespace():
    """A symbol that was in NIFTY50 but never in NIFTY200 must be dropped
    when the diagnostic queries with index_name='NIFTY200'."""
    features = _synth_features(250)
    reg = MembershipRegistry([
        MembershipInterval("NIFTY50", "BANKX", date(2018, 1, 1), None),
    ])
    out_n200 = apply_pit_row_filter(features, "BANKX", reg, index_name="NIFTY200")
    out_n50  = apply_pit_row_filter(features, "BANKX", reg, index_name="NIFTY50")
    assert len(out_n200) == 0, "NIFTY50 membership does not satisfy NIFTY200 query"
    assert len(out_n50) == len(features), "NIFTY50 membership query keeps all rows"


# ── Multi-spell rejoiner: gap in the middle ─────────────────────────────────
def test_apply_pit_row_filter_handles_rejoiner_with_gap():
    """A symbol that left the index in 2020 and rejoined in 2023 must have
    a HOLE in its kept-rows around 2021-2022 — not a contiguous range."""
    features = _synth_features(n_days=1500, start="2018-01-02")  # spans into mid-2023
    reg = MembershipRegistry([
        MembershipInterval("NIFTY200", "REJOINER", date(2018, 1, 1), date(2020, 6, 30)),
        MembershipInterval("NIFTY200", "REJOINER", date(2023, 1, 1), None),
    ])
    out = apply_pit_row_filter(features, "REJOINER", reg)
    kept_dates = pd.DatetimeIndex(out.index).date
    # First spell present.
    assert any(d == date(2019, 1, 2) for d in kept_dates)
    # Gap dropped.
    assert not any(date(2020, 7, 1) <= d <= date(2022, 12, 31) for d in kept_dates), \
        "rows in the membership gap must be dropped"
    # Second spell present — at least one kept row inside it.
    assert any(date(2023, 1, 1) <= d <= date(2023, 12, 31) for d in kept_dates), \
        "post-rejoin rows must be present (any 2023 date qualifies)"


# ── Column / index integrity ────────────────────────────────────────────────
def test_apply_pit_row_filter_preserves_column_dtype_and_index_identity():
    """The filter is a row-mask only — column dtypes and index type must
    survive untouched so downstream feature_engineering / NaN handling
    behaves the same."""
    features = _synth_features(100)
    features["int_col"] = np.arange(100, dtype=np.int64)
    reg = MembershipRegistry([
        MembershipInterval("NIFTY200", "X", date(2018, 1, 1), None),
    ])
    out = apply_pit_row_filter(features, "X", reg)
    assert out["int_col"].dtype == np.int64, "int dtype must be preserved"
    assert isinstance(out.index, pd.DatetimeIndex), "DatetimeIndex type preserved"
    assert list(out.columns) == list(features.columns), "column order unchanged"


# ── Integration: build_dataset_with_horizons and build_training_dataset accept the param ─
def test_build_dataset_with_horizons_signature_has_pit_params():
    """The dataset builder must accept pit_universe / pit_index_name with
    sensible defaults so all callers keep working without modification."""
    import inspect
    from quant_engine.ml.diagnostic import build_dataset_with_horizons
    sig = inspect.signature(build_dataset_with_horizons)
    assert "pit_universe" in sig.parameters
    assert "pit_index_name" in sig.parameters
    assert sig.parameters["pit_universe"].default is None, \
        "PIT must be off by default — legacy behaviour preserved"


def test_build_training_dataset_signature_has_pit_params():
    import inspect
    from quant_engine.ml.trainer import build_training_dataset
    sig = inspect.signature(build_training_dataset)
    assert "pit_universe" in sig.parameters
    assert "pit_index_name" in sig.parameters
    assert sig.parameters["pit_universe"].default is None


def test_run_diagnostic_signature_has_pit_params():
    import inspect
    from quant_engine.ml.diagnostic import run_diagnostic
    sig = inspect.signature(run_diagnostic)
    assert "pit_universe" in sig.parameters
    assert sig.parameters["pit_universe"].default is None, \
        "run_diagnostic must default to PIT-off"


# ── load_all_symbols PIT mode ───────────────────────────────────────────────
def test_load_all_symbols_signature_accepts_as_of():
    """The live-trading screen path: load_all_symbols(as_of=date) must
    restrict to PIT members rather than returning the historical superset."""
    import inspect
    from quant_engine.data.loader import load_all_symbols
    sig = inspect.signature(load_all_symbols)
    assert "as_of" in sig.parameters
    assert sig.parameters["as_of"].default is None, \
        "as_of must default to None so existing callers keep historical-superset behaviour"
