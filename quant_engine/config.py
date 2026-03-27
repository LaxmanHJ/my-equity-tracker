"""
Quant Engine Configuration
"""
import os
from pathlib import Path

# The Node.js app's SQLite database path (kept as local fallback)
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "portfolio.db"

# Turso cloud database credentials (loaded from .env)
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# FastAPI settings
HOST = "0.0.0.0"
PORT = 5001

# Factor weights (must sum to 1.0)
FACTOR_WEIGHTS = {
    "momentum":          0.25,
    "mean_reversion":    0.15,
    "rsi":               0.15,
    "macd":              0.15,
    "volatility":        0.10,
    "volume":            0.10,
    "relative_strength": 0.10,
}

# Signal thresholds on the -100 to +100 composite score
LONG_THRESHOLD = 40
SHORT_THRESHOLD = -40

# Risk-free rate for Sharpe calculations (Indian govt bond ~6%)
RISK_FREE_RATE = 0.06

# ML model storage path
ML_MODEL_DIR = Path(__file__).parent / "ml" / "models"
