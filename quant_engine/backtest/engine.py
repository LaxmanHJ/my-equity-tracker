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

    def run(self, signals: pd.Series) -> pd.Series:
        """
        Execute the backtest given an array of signals.
        signals: A Pandas Series (1=Long, -1=Short, 0=Flat)
        Returns the Equity Curve as a Pandas Series.
        """
        df = self.data.copy()
        df['signal'] = signals
        
        # We assume executing on the CLOSE of the next bar after signal generation
        # E.g., signal on day T -> position starts end of day T (held for T+1 returns)
        # Shift signals by 1 to represent the actual held position during the day's return
        df['position'] = df['signal'].shift(1).fillna(0)
        
        # Calculate single-period daily returns from closing prices
        df['asset_returns'] = df['close'].pct_change()
        
        # Gross returns are the asset returns multiplied by our held position
        df['strategy_returns'] = df['position'] * df['asset_returns']
        
        # Calculate transaction costs when position changes
        # e.g. 0 -> 1 is 1 unit of turnover. 1 -> -1 is 2 units of turnover.
        df['trades'] = df['position'].diff().abs().fillna(0)
        
        transaction_costs = df['trades'] * (self.commission + self.slippage)
        
        # Net returns
        df['net_returns'] = df['strategy_returns'] - transaction_costs
        
        # Cumulative Equity Curve
        # Initial capital * cumulative product of (1 + net_returns)
        df['equity_curve'] = self.initial_capital * (1 + df['net_returns']).cumprod()
        # Set the very first row to initial capital (since return is NaN)
        df.iloc[0, df.columns.get_loc('equity_curve')] = self.initial_capital
        
        return df['equity_curve']
