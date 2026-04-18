"""
Quant Engine Configuration
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Turso cloud database credentials (loaded from .env) — single source of truth.
# The legacy local SQLite (data/portfolio.db) was retired on 2026-04-18.
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# FastAPI settings
HOST = "0.0.0.0"
PORT = 5001

# Factor weights (must sum to 1.0)
# Calibrated against ML feature importances (metadata.json):
#   volatility=12.6%, rsi=11.7%, bollinger=9.3%, macd=6.8%, rs=7.0%
# mean_reversion replaced by bollinger (same contrarian intent, volatility-adaptive).
# RSI direction flipped to trend-confirmation (high RSI = bullish, not overbought).
FACTOR_WEIGHTS = {
    "momentum":          0.20,
    "bollinger":         0.15,
    "rsi":               0.20,
    "macd":              0.12,
    "volatility":        0.20,
    "volume":            0.05,
    "relative_strength": 0.08,
}

# Signal thresholds on the -100 to +100 composite score
LONG_THRESHOLD = 40
SHORT_THRESHOLD = -40

# Risk-free rate for Sharpe calculations (Indian govt bond ~6%)
RISK_FREE_RATE = 0.06

# ML model storage path
ML_MODEL_DIR = Path(__file__).parent / "ml" / "models"

# Industry → NSE sector index mapping (used for sector_rotation ML feature)
# If a stock's industry has no exact match, fall back to "Nifty 500"
INDUSTRY_TO_NSE_INDEX = {
    "Information Technology":   "Nifty IT",
    "Power":                    "Nifty Energy",
    "Steel":                    "Nifty Metal",
    "Banking":                  "Nifty Bank",
    "Financial Services":       "Nifty Financial Services",
    "Non-Banking Finance":      "Nifty Financial Services",
    "Chemicals":                "Nifty Chemicals",
    "Sugar":                    "Nifty FMCG",
    "Telecom":                  "Nifty IT",          # closest proxy for CPaaS/tech
    "Automobiles":              "Nifty Auto",
    "Pharmaceuticals":          "Nifty Pharma",
    "Real Estate":              "Nifty Realty",
    "Consumer Goods":           "Nifty FMCG",
    "Infrastructure":           "Nifty Infrastructure",
}
