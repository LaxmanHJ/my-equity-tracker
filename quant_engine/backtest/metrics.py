import pandas as pd
import numpy as np
from quant_engine.config import RISK_FREE_RATE

def calculate_cagr(portfolio_start_value: float, portfolio_end_value: float, years: float) -> float:
    """Compound Annual Growth Rate"""
    if years <= 0 or portfolio_start_value <= 0:
        return 0.0
    return (portfolio_end_value / portfolio_start_value) ** (1 / years) - 1

def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = RISK_FREE_RATE, trading_days: int = 252) -> float:
    """Annualized Sharpe Ratio based on daily log returns or simple returns"""
    if returns.std() == 0:
        return 0.0
    
    # Calculate daily risk-free rate
    daily_rf = risk_free_rate / trading_days
    excess_returns = returns - daily_rf
    
    annualized_return = excess_returns.mean() * trading_days
    annualized_vol = returns.std() * np.sqrt(trading_days)
    
    if annualized_vol == 0:
        return 0.0
        
    return annualized_return / annualized_vol

def calculate_sortino_ratio(returns: pd.Series, risk_free_rate: float = RISK_FREE_RATE, trading_days: int = 252) -> float:
    """Annualized Sortino Ratio using downside deviation"""
    daily_rf = risk_free_rate / trading_days
    excess_returns = returns - daily_rf
    
    # Filter for downside
    downside_returns = excess_returns[excess_returns < 0]
    if len(downside_returns) == 0 or downside_returns.std() == 0:
        return 0.0 # Or theoretically infinity
        
    annualized_return = excess_returns.mean() * trading_days
    # Downside deviation uses population standard deviation of negative returns assuming target is 0
    # A standard shortcut is root-mean-square of negative returns
    downside_dev = np.sqrt(np.mean(downside_returns ** 2)) * np.sqrt(trading_days)
    
    if downside_dev == 0:
        return 0.0
        
    return annualized_return / downside_dev

def calculate_max_drawdown(equity_curve: pd.Series) -> float:
    """Calculate the maximum peak-to-trough decline as a percentage."""
    if equity_curve.empty:
        return 0.0
    # Cumulative max tracks the peak wealth so far
    rolling_max = equity_curve.cummax()
    # Drawdown is the distance from the current wealth to the peak
    drawdown = (equity_curve - rolling_max) / rolling_max
    return drawdown.min() # Max drawdown is the most negative value

def calculate_metrics(equity_curve: pd.Series, initial_capital: float = 10000) -> dict:
    """
    Given an equity curve (Series of portfolio values indexed by datetime),
    calculates all summary statistics.
    """
    if equity_curve.empty or len(equity_curve) < 2:
        return {}

    # Calculate daily simple returns
    daily_returns = equity_curve.pct_change().dropna()
    
    start_date = equity_curve.index[0]
    end_date = equity_curve.index[-1]
    years = (end_date - start_date).days / 365.25
    
    final_capital = equity_curve.iloc[-1]
    
    cagr = calculate_cagr(initial_capital, final_capital, years)
    sharpe = calculate_sharpe_ratio(daily_returns)
    sortino = calculate_sortino_ratio(daily_returns)
    max_dd = calculate_max_drawdown(equity_curve)
    
    total_return = (final_capital - initial_capital) / initial_capital

    return {
        "start_capital": round(initial_capital, 2),
        "end_capital": round(final_capital, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "years_tested": round(years, 2)
    }
