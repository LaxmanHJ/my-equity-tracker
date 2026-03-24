import pandas as pd
import numpy as np

class VectorizedBacktester:
    """
    Pandas-based vectorized backtesting engine for rapid strategy evaluation.
    Evaluates historical performance synchronously across entire arrays.
    """
    def __init__(self, data: pd.DataFrame, initial_capital: float = 10000.0, commission: float = 0.001, slippage: float = 0.0005):
        """
        data: DataFrame with OHLCV containing a DateTimeIndex, ordered chronologically.
        """
        self.data = data.copy()
        self.initial_capital = initial_capital
        # Commission per trade (e.g. 0.1% = 0.001)
        self.commission = commission
        # Slippage as a fraction of price (e.g. 0.05% = 0.0005)
        self.slippage = slippage

    def run(self, signals: pd.Series) -> dict:
        """
        Execute the backtest given an array of signals.
        signals: A Pandas Series (1=Long, 0=Flat, -1=Exit/Flat)
        Note: This is a long-only engine (Indian retail can't short overnight).
              Signal -1 is treated as "exit to cash" (same as 0), not as a short.
        Returns a dict with:
            - 'strategy': Strategy Equity Curve (pd.Series)
            - 'baseline': Buy & Hold Equity Curve (pd.Series)
        """
        df = self.data.copy()
        df['signal'] = signals
        
        # We assume executing on the CLOSE of the next bar after signal generation
        # E.g., signal on day T -> position starts end of day T (held for T+1 returns)
        # Shift signals by 1 to represent the actual held position during the day's return
        # Long-only: clip to [0, 1] — signal -1 (sell) means exit to cash, not short
        df['position'] = df['signal'].shift(1).fillna(0).clip(lower=0)
        
        # Calculate single-period daily returns from closing prices
        df['asset_returns'] = df['close'].pct_change().fillna(0)
        
        # --- Strategy Equity Curve ---
        # Gross returns are the asset returns multiplied by our held position
        df['strategy_returns'] = df['position'] * df['asset_returns']
        
        # Calculate transaction costs when position changes
        # e.g. 0 -> 1 is 1 unit of turnover
        df['trades'] = df['position'].diff().abs().fillna(0)
        
        transaction_costs = df['trades'] * (self.commission + self.slippage)
        
        # Net returns (fillna(0) prevents NaN on row 0 from corrupting cumprod)
        df['net_returns'] = (df['strategy_returns'] - transaction_costs).fillna(0)
        
        # Cumulative Equity Curve
        df['equity_curve'] = self.initial_capital * (1 + df['net_returns']).cumprod()
        
        # --- Buy & Hold Baseline ---
        # What if you just bought and held from day 1, no trades after entry
        df['baseline'] = self.initial_capital * (1 + df['asset_returns']).cumprod()
        
        return {
            'strategy': df['equity_curve'],
            'baseline': df['baseline']
        }
