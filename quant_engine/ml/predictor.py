"""
ML Predictor for The Sicilian Engine.

Loads the trained Random Forest from disk and exposes a single predict()
function.  Returns None gracefully if the model hasn't been trained yet,
allowing the engine to fall back to the linear weighted approach.
"""
import json
import logging
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from quant_engine.config import ML_MODEL_DIR

logger = logging.getLogger(__name__)

# Map numeric class labels → human-readable verdicts
CLASS_MAP = {-1: "SELL", 0: "HOLD", 1: "BUY"}

FEATURE_COLS = [
    "rsi",
    "macd",
    "trend_ma",
    "bollinger",
    "volume",
    "volatility",
    "relative_strength",
    "sector_rotation",
    "analyst_consensus",
    "vix_regime",
    "nifty_trend",
    "markov_regime",
    "delivery_score",
    "fii_flow_score",
    "fii_fo_score",
]

_model_cache = None   # module-level cache so we only load from disk once


def _load_model():
    global _model_cache
    model_path = ML_MODEL_DIR / "sicilian_rf.pkl"
    if not model_path.exists():
        return None
    if _model_cache is None:
        logger.info("Loading Sicilian ML model from %s", model_path)
        _model_cache = joblib.load(model_path)
    return _model_cache


def invalidate_cache():
    """Call this after re-training so the next predict() reloads from disk."""
    global _model_cache
    _model_cache = None


def is_model_available() -> bool:
    return (ML_MODEL_DIR / "sicilian_rf.pkl").exists()


def get_metadata() -> Optional[dict]:
    meta_path = ML_MODEL_DIR / "metadata.json"
    if not meta_path.exists():
        return None
    with open(meta_path) as fh:
        return json.load(fh)


def predict(sub_scores: dict) -> Optional[dict]:
    """
    Run the trained RF model on a set of technical sub-scores.

    Args:
        sub_scores: dict with at minimum the 8 FEATURE_COLS keys,
                    each value on the –1 to +1 scale (as produced by engine.py).

    Returns:
        dict with keys:
            verdict     – "BUY" / "SELL" / "HOLD"
            confidence  – 0–100 (the winning class probability × 100)
            probabilities – {"BUY": float, "SELL": float, "HOLD": float}
        or None if the model isn't trained yet.
    """
    model = _load_model()
    if model is None:
        return None

    # Build feature vector in the exact column order the model was trained on
    X = pd.DataFrame([[sub_scores.get(col, 0.0) for col in FEATURE_COLS]], columns=FEATURE_COLS)

    proba = model.predict_proba(X)[0]          # shape: (n_classes,)
    classes = model.classes_                    # e.g. [-1, 0, 1]

    # Build human-readable probability dict
    prob_dict = {CLASS_MAP[c]: round(float(p), 4) for c, p in zip(classes, proba)}

    # Winning class
    winning_idx = int(np.argmax(proba))
    winning_class = int(classes[winning_idx])
    verdict = CLASS_MAP[winning_class]
    confidence = round(float(proba[winning_idx]) * 100, 1)

    return {
        "verdict": verdict,
        "confidence": confidence,
        "probabilities": prob_dict,
    }
