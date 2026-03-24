"""
ML Router — endpoints for training and inspecting the Sicilian ML model.

POST /api/ml/train   – triggers the training pipeline (runs synchronously;
                       call from a background job for large portfolios)
GET  /api/ml/status  – returns whether the model is trained + its metadata
                       (feature importances, CV accuracy, training date, etc.)
"""
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException

from quant_engine.ml import predictor
from quant_engine.ml.trainer import run_training_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ml", tags=["ml"])

# Simple in-process flag so the UI can poll while training runs in the background
_training_state: dict = {"status": "idle", "error": None}


def _do_train():
    global _training_state
    _training_state = {"status": "running", "error": None}
    try:
        metadata = run_training_pipeline()
        predictor.invalidate_cache()           # force reload on next predict()
        _training_state = {"status": "ready", "error": None, "metadata": metadata}
        logger.info("ML training complete. CV accuracy: %.3f", metadata["cv_accuracy_mean"])
    except Exception as exc:
        logger.exception("ML training failed")
        _training_state = {"status": "error", "error": str(exc)}


@router.post("/train")
async def trigger_training(background_tasks: BackgroundTasks):
    """
    Start (or re-start) the ML training pipeline in the background.
    Returns immediately; poll GET /api/ml/status to check progress.
    """
    if _training_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Training is already in progress.")
    background_tasks.add_task(_do_train)
    return {"message": "Training started in background. Poll /api/ml/status for progress."}


@router.get("/status")
async def get_status():
    """
    Returns current training state and, if a model exists, its metadata
    (feature importances, CV accuracy, class distribution, training date).
    """
    model_ready = predictor.is_model_available()
    metadata = predictor.get_metadata() if model_ready else None

    return {
        "model_available": model_ready,
        "training_state": _training_state.get("status", "idle"),
        "error": _training_state.get("error"),
        "metadata": metadata,
    }
