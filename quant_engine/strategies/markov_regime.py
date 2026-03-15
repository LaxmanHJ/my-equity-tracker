"""
Markov Chain Regime Detection for Market Indexes.
Classifies daily market states into Bull / Sideways / Bear regimes
and builds a transition probability matrix to predict regime changes.
"""
import numpy as np
import pandas as pd
from typing import Dict, List


REGIMES = ["Bear", "Sideways", "Bull"]

# Daily return thresholds for regime classification
BULL_THRESHOLD = 0.005    # > +0.5%
BEAR_THRESHOLD = -0.005   # < -0.5%


def _classify_return(ret: float) -> int:
    """Map a daily return to a regime index: 0=Bear, 1=Sideways, 2=Bull."""
    if ret < BEAR_THRESHOLD:
        return 0
    elif ret > BULL_THRESHOLD:
        return 2
    return 1


def _build_transition_matrix(states: np.ndarray) -> np.ndarray:
    """
    Build a 3x3 transition probability matrix from observed state sequence.
    matrix[i][j] = P(next_state = j | current_state = i)
    """
    n_states = 3
    counts = np.zeros((n_states, n_states), dtype=float)

    for i in range(len(states) - 1):
        counts[states[i], states[i + 1]] += 1

    # Normalize rows to probabilities
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1  # avoid division by zero
    matrix = counts / row_sums
    return matrix


def calculate(df: pd.DataFrame) -> dict:
    """
    Run Markov Chain regime analysis on index price data.

    Args:
        df: DataFrame with 'close' column, indexed by date.

    Returns:
        dict with current regime, transition matrix, predictions, and history.
    """
    if df.empty or len(df) < 30:
        return {
            "current_regime": "Unknown",
            "regime_streak": 0,
            "transition_matrix": [],
            "next_day_probabilities": {},
            "regime_distribution": {},
            "regime_history_30d": [],
            "error": "Insufficient data (need >= 30 days)",
        }

    close = df["close"].astype(float)
    returns = close.pct_change().dropna()

    # Classify each day into a regime
    states = np.array([_classify_return(r) for r in returns])

    # Build transition matrix
    trans_matrix = _build_transition_matrix(states)

    # Current regime (last observed state)
    current_state = states[-1]
    current_regime = REGIMES[current_state]

    # Calculate streak (how many consecutive days in current regime)
    streak = 1
    for i in range(len(states) - 2, -1, -1):
        if states[i] == current_state:
            streak += 1
        else:
            break

    # Next-day probabilities from current state
    next_probs = trans_matrix[current_state]
    next_day_probs = {
        REGIMES[i]: round(float(next_probs[i]), 4)
        for i in range(3)
    }

    # Regime distribution over entire history
    unique, counts = np.unique(states, return_counts=True)
    total = len(states)
    distribution = {}
    for i in range(3):
        idx = np.where(unique == i)[0]
        pct = float(counts[idx[0]]) / total * 100 if len(idx) > 0 else 0.0
        distribution[REGIMES[i]] = round(pct, 1)

    # Last 30 days of regime history
    dates = returns.index[-30:]
    recent_states = states[-30:]
    regime_history = [
        {"date": d.strftime("%Y-%m-%d"), "regime": REGIMES[s]}
        for d, s in zip(dates, recent_states)
    ]

    # Format transition matrix for JSON
    matrix_formatted = []
    for i in range(3):
        row = {}
        for j in range(3):
            row[REGIMES[j]] = round(float(trans_matrix[i][j]), 4)
        matrix_formatted.append({"from": REGIMES[i], "to": row})

    return {
        "current_regime": current_regime,
        "regime_streak": int(streak),
        "transition_matrix": matrix_formatted,
        "next_day_probabilities": next_day_probs,
        "regime_distribution": distribution,
        "regime_history_30d": regime_history,
    }
