"""
Tests for scoring/ic_weights._constrain_weights — the IC_MAX_W cap must hold.

Regression tests for the 2026-05-13 cap-vs-renormalise bug: when only one
factor passed IC_FLOOR, the final renormalisation undid the cap and the
Active Factor Weights panel showed 100% concentration on a single factor.
"""
import pytest

from quant_engine.scoring.ic_weights import _constrain_weights, IC_MAX_W
from quant_engine.config import FACTOR_WEIGHTS


FACTORS = list(FACTOR_WEIGHTS.keys())


def _clipped(**positive):
    """Build a clipped-IC dict: named factors positive, the rest zero."""
    d = {k: 0.0 for k in FACTORS}
    d.update(positive)
    return d


class TestSingleSurvivor:
    """The original bug: one factor above floor must NOT take 100%."""

    def test_cap_holds(self):
        w = _constrain_weights(_clipped(momentum=0.05), adaptive_budget=1.0)
        assert w["momentum"] == pytest.approx(IC_MAX_W)

    def test_residual_spread_to_zeroed_by_static_weights(self):
        w = _constrain_weights(_clipped(momentum=0.05), adaptive_budget=1.0)
        residual = 1.0 - IC_MAX_W
        zeroed_static = {k: FACTOR_WEIGHTS[k] for k in FACTORS if k != "momentum"}
        zpool = sum(zeroed_static.values())
        for k, sw in zeroed_static.items():
            assert w[k] == pytest.approx(residual * sw / zpool)

    def test_total_is_budget(self):
        w = _constrain_weights(_clipped(momentum=0.05), adaptive_budget=1.0)
        assert sum(w.values()) == pytest.approx(1.0)


class TestTwoSurvivors:
    def test_both_capped_residual_to_zeroed(self):
        # Two factors split the budget 50/50 → both exceed the 0.40 cap.
        w = _constrain_weights(_clipped(momentum=0.05, rsi=0.05), adaptive_budget=1.0)
        assert w["momentum"] == pytest.approx(IC_MAX_W)
        assert w["rsi"] == pytest.approx(IC_MAX_W)
        assert sum(w.values()) == pytest.approx(1.0)
        # Residual 0.20 lands on the six zero-IC factors, not back on the capped two.
        assert all(w[k] > 0 for k in FACTORS if k not in ("momentum", "rsi"))

    def test_spill_flows_to_uncapped_survivor(self):
        # momentum dominates (would take 0.8) — its spill belongs to rsi
        # until rsi itself hits the cap.
        w = _constrain_weights(_clipped(momentum=0.08, rsi=0.02), adaptive_budget=1.0)
        assert w["momentum"] == pytest.approx(IC_MAX_W)
        assert w["rsi"] == pytest.approx(IC_MAX_W)


class TestNoCapNeeded:
    def test_proportional_passthrough(self):
        ics = _clipped(momentum=0.03, rsi=0.03, macd=0.03, volatility=0.03)
        w = _constrain_weights(ics, adaptive_budget=1.0)
        for k in ("momentum", "rsi", "macd", "volatility"):
            assert w[k] == pytest.approx(0.25)
        assert sum(w.values()) == pytest.approx(1.0)

    def test_no_factor_exceeds_cap(self):
        ics = _clipped(momentum=0.06, rsi=0.04, macd=0.02, volatility=0.02, volume=0.02)
        w = _constrain_weights(ics, adaptive_budget=1.0)
        cap = IC_MAX_W * 1.0
        assert all(v <= cap + 1e-9 for v in w.values())
        assert sum(w.values()) == pytest.approx(1.0)


class TestReducedBudget:
    def test_cap_scales_with_budget(self):
        # With a 0.9 adaptive budget (0.1 reserved), the cap is 0.40 × 0.9.
        w = _constrain_weights(_clipped(momentum=0.05), adaptive_budget=0.9)
        assert w["momentum"] == pytest.approx(IC_MAX_W * 0.9)
        assert sum(w.values()) == pytest.approx(0.9)
