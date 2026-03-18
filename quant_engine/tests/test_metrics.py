import unittest
import pandas as pd
import numpy as np
from quant_engine.backtest.metrics import calculate_cagr, calculate_sharpe_ratio, calculate_max_drawdown

class TestMetrics(unittest.TestCase):
    def test_cagr(self):
        # Doubled money in 1 year = 100%
        self.assertAlmostEqual(calculate_cagr(100, 200, 1), 1.0)
        # 10% gain in 1 year
        self.assertAlmostEqual(calculate_cagr(100, 110, 1), 0.10)
        # Multi-year: 100 -> 144 in 2 years is roughly 20%
        self.assertAlmostEqual(calculate_cagr(100, 144, 2), 0.20)

    def test_max_drawdown(self):
        # 100 -> 120 -> 90 -> 150
        # Peak: 120, Trough: 90
        # DD = (90-120)/120 = -30/120 = -0.25
        curve = pd.Series([100, 120, 90, 150])
        self.assertAlmostEqual(calculate_max_drawdown(curve), -0.25)
        
        # Always up
        curve2 = pd.Series([100, 110, 120])
        self.assertAlmostEqual(calculate_max_drawdown(curve2), 0.0)

    def test_sharpe_ratio(self):
        # Perfect steady returns, zero volatility -> should handle without div by zero
        steady = pd.Series([0.01, 0.01, 0.01, 0.01])
        self.assertEqual(calculate_sharpe_ratio(steady), 0.0)

if __name__ == '__main__':
    unittest.main()
