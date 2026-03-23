"""
Unit tests for The Sicilian decision engine.
"""
import unittest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

# We import the sub-score helpers and the main engine
from quant_engine.sicilian.engine import (
    _score_composite,
    _score_rsi,
    _score_macd,
    _score_trend,
    _score_bollinger,
    _score_volume,
    _score_volatility,
    _score_valuation,
    _score_financial_health,
    _score_growth,
    BUY_THRESHOLD,
    SELL_THRESHOLD,
    SICILIAN_WEIGHTS,
)


class TestSubScores(unittest.TestCase):
    """Test individual sub-score calculations."""

    def test_score_composite_range(self):
        # Composite score of +100 → +1.0, -100 → -1.0, 0 → 0
        self.assertAlmostEqual(_score_composite(100), 1.0)
        self.assertAlmostEqual(_score_composite(-100), -1.0)
        self.assertAlmostEqual(_score_composite(0), 0.0)
        self.assertAlmostEqual(_score_composite(50), 0.5)

    def test_score_composite_clipping(self):
        # Values beyond ±100 should be clipped
        self.assertAlmostEqual(_score_composite(200), 1.0)
        self.assertAlmostEqual(_score_composite(-200), -1.0)

    def test_score_rsi_oversold(self):
        # RSI = 20 → strong buy (+1.0)
        score = _score_rsi(20)
        self.assertAlmostEqual(score, 1.0)

    def test_score_rsi_overbought(self):
        # RSI = 80 → strong sell (-1.0)
        score = _score_rsi(80)
        self.assertAlmostEqual(score, -1.0)

    def test_score_rsi_neutral(self):
        # RSI = 50 → neutral (0)
        score = _score_rsi(50)
        self.assertAlmostEqual(score, 0.0)

    def test_score_rsi_none(self):
        self.assertAlmostEqual(_score_rsi(None), 0.0)

    def test_score_macd_bullish(self):
        macd_info = {"histogram": 5.0, "crossover": "BULLISH"}
        score = _score_macd(macd_info, 1000)
        self.assertGreater(score, 0)  # bullish crossover = positive

    def test_score_macd_bearish(self):
        macd_info = {"histogram": -5.0, "crossover": "BEARISH"}
        score = _score_macd(macd_info, 1000)
        self.assertLess(score, 0)

    def test_score_trend_bullish(self):
        # Price above both SMAs, SMA20 > SMA50
        score = _score_trend(110, 100, 90)
        self.assertAlmostEqual(score, 1.0)

    def test_score_trend_bearish(self):
        # Price below both SMAs, SMA20 < SMA50
        score = _score_trend(80, 90, 100)
        self.assertAlmostEqual(score, -1.0)

    def test_score_trend_mixed(self):
        # Price above SMA20 but below SMA50, SMA20 < SMA50
        score = _score_trend(95, 90, 100)
        self.assertEqual(score, -0.5)

    def test_score_trend_none_sma(self):
        self.assertAlmostEqual(_score_trend(100, None, 95), 0.0)

    def test_score_bollinger_oversold(self):
        # %B = 0 → strong buy
        score = _score_bollinger(0.0)
        self.assertAlmostEqual(score, 1.0)

    def test_score_bollinger_overbought(self):
        # %B = 1 → strong sell
        score = _score_bollinger(1.0)
        self.assertAlmostEqual(score, -1.0)

    def test_score_bollinger_neutral(self):
        # %B = 0.5 → neutral
        score = _score_bollinger(0.5)
        self.assertAlmostEqual(score, 0.0)


class TestWeightsSum(unittest.TestCase):
    """Verify that weights sum to 1.0."""

    def test_weights_sum(self):
        total = sum(SICILIAN_WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0)

    def test_weight_count(self):
        self.assertEqual(len(SICILIAN_WEIGHTS), 11)


class TestThresholds(unittest.TestCase):
    """Verify thresholds are symmetric and sensible."""

    def test_buy_threshold_positive(self):
        self.assertGreater(BUY_THRESHOLD, 0)

    def test_sell_threshold_negative(self):
        self.assertLess(SELL_THRESHOLD, 0)

    def test_thresholds_symmetric(self):
        self.assertAlmostEqual(abs(BUY_THRESHOLD), abs(SELL_THRESHOLD))


class TestVolumeScore(unittest.TestCase):
    """Test volume scoring with synthetic data."""

    def _make_df(self, n=30, spike=False):
        dates = pd.date_range("2025-01-01", periods=n)
        close = pd.Series(np.linspace(100, 110, n), index=dates, name="close")
        vol_base = 1000
        if spike:
            volumes = [vol_base] * (n - 1) + [vol_base * 3]  # 3x spike on last day
        else:
            volumes = [vol_base] * n
        volume = pd.Series(volumes, index=dates, name="volume")
        return pd.DataFrame({"close": close, "volume": volume})

    def test_no_spike(self):
        df = self._make_df(30, spike=False)
        score = _score_volume(df)
        self.assertAlmostEqual(score, 0.0, places=1)

    def test_bullish_spike(self):
        df = self._make_df(30, spike=True)
        score = _score_volume(df)
        self.assertGreater(score, 0)  # Uptrend with volume spike


class TestFundamentalScores(unittest.TestCase):
    """Test fundamental sub-score calculations."""

    # ── Valuation ──
    def test_valuation_undervalued(self):
        fund = {"pe_ratio": 10, "pb_ratio": 0.8, "price_to_sales": 0.5}
        score = _score_valuation(fund)
        self.assertGreater(score, 0.4)  # Should be strongly positive

    def test_valuation_overvalued(self):
        fund = {"pe_ratio": 45, "pb_ratio": 6, "price_to_sales": 10}
        score = _score_valuation(fund)
        self.assertLess(score, -0.4)  # Should be strongly negative

    def test_valuation_none(self):
        self.assertAlmostEqual(_score_valuation(None), 0.0)
        self.assertAlmostEqual(_score_valuation({}), 0.0)

    # ── Financial Health ──
    def test_health_strong(self):
        fund = {"current_ratio": 2.5, "debt_to_equity": 0.3, "interest_coverage": 8, "free_cash_flow": 1000}
        score = _score_financial_health(fund)
        self.assertGreater(score, 0.3)

    def test_health_weak(self):
        fund = {"current_ratio": 0.5, "debt_to_equity": 3.0, "interest_coverage": 1.0, "free_cash_flow": -500}
        score = _score_financial_health(fund)
        self.assertLess(score, -0.3)

    def test_health_none(self):
        self.assertAlmostEqual(_score_financial_health(None), 0.0)

    # ── Growth ──
    def test_growth_strong(self):
        fund = {"revenue_growth_5y": 20, "eps_growth_5y": 25, "revenue_growth_3y": 18, "eps_growth_3y": 22}
        score = _score_growth(fund)
        self.assertGreater(score, 0.5)

    def test_growth_declining(self):
        fund = {"revenue_growth_5y": -5, "eps_growth_5y": -10, "revenue_growth_3y": -8, "eps_growth_3y": -15}
        score = _score_growth(fund)
        self.assertLess(score, -0.3)

    def test_growth_none(self):
        self.assertAlmostEqual(_score_growth(None), 0.0)

    def test_growth_margin_trend(self):
        fund = {"net_profit_margin_ttm": 15, "net_profit_margin_5y_avg": 10}
        score = _score_growth(fund)
        self.assertGreater(score, 0)  # Expanding margins = positive


if __name__ == "__main__":
    unittest.main()

