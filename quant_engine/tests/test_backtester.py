import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from quant_engine.backtest.engine import VectorizedBacktester
from quant_engine.backtest.metrics import calculate_metrics

def generate_mock_data(days=252):
    """Generate 1 year of random price data"""
    dates = pd.date_range(end=datetime.now(), periods=days)
    # Start at 100, generate daily log returns with slight upward drift, convert to prices
    returns = np.random.normal(0.0005, 0.015, days)
    prices = 100 * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({
        'open': prices * 0.99,
        'high': prices * 1.01,
        'low': prices * 0.98,
        'close': prices,
        'volume': np.random.randint(1000, 10000, days)
    }, index=dates)
    return df

def test_buy_and_hold():
    """A strategy that just holds 1 unit every day."""
    df = generate_mock_data(500) # 2 years
    
    # 1. Buy and hold signals (always 1)
    signals = pd.Series(1, index=df.index)
    
    # 2. Run engine
    print(f"Starting Price: {df['close'].iloc[0]:.2f}")
    print(f"Ending Price: {df['close'].iloc[-1]:.2f}")
    
    # Zero friction just to verify raw math first
    backtester = VectorizedBacktester(df, initial_capital=10000.0, commission=0.0, slippage=0.0)
    equity_curve = backtester.run(signals)
    
    # Calculate metrics
    metrics = calculate_metrics(equity_curve)
    
    print("\n--- Buy & Hold Metrics (Zero Friction) ---")
    for k, v in metrics.items():
        print(f"{k}: {v}")

if __name__ == '__main__':
    test_buy_and_hold()
