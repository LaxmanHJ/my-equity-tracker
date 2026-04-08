"""
ML Router — endpoints for training and inspecting the Sicilian ML model.

POST /api/ml/train       – triggers the training pipeline (runs in background)
GET  /api/ml/status      – returns whether the model is trained + its metadata
POST /api/ml/diagnostic  – runs walk-forward purged CV diagnostic in background
                           (multi-horizon IC on the full price history — does
                           NOT retrain the live model)
GET  /api/ml/diagnostic  – returns the last diagnostic result + run state
"""
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException

from quant_engine.ml import diagnostic, predictor
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


# ── Diagnostic (multi-horizon historical IC) ─────────────────────────────────
# This path does NOT touch the live model. It runs the production model's
# hyperparameters against a walk-forward purged CV over the full price
# history and reports IC/ICIR/hit-rate at 1d/5d/10d/20d horizons.
# See quant_engine/ml/diagnostic.py for full docstring and wiki references.

_diagnostic_state: dict = {"status": "idle", "error": None}


def _do_diagnostic():
    global _diagnostic_state
    _diagnostic_state = {"status": "running", "error": None}
    try:
        result = diagnostic.run_diagnostic()
        _diagnostic_state = {"status": "ready", "error": None}
        logger.info(
            "ML diagnostic complete. Folds: %d, samples: %d",
            result.get("n_folds_completed", 0),
            result.get("n_samples_total", 0),
        )
    except Exception as exc:
        logger.exception("ML diagnostic failed")
        _diagnostic_state = {"status": "error", "error": str(exc)}


@router.post("/diagnostic")
async def trigger_diagnostic(background_tasks: BackgroundTasks):
    """
    Run the multi-horizon walk-forward purged CV diagnostic in the background.

    Does NOT retrain the live model — it only measures how the current model
    generalises at 1d/5d/10d/20d horizons over the full price history.
    The run takes several minutes; poll GET /api/ml/diagnostic for results.
    """
    if _diagnostic_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Diagnostic is already running.")
    background_tasks.add_task(_do_diagnostic)
    return {"message": "Diagnostic started. Poll GET /api/ml/diagnostic for results."}


@router.get("/diagnostic")
async def get_diagnostic():
    """
    Return the most recent diagnostic result from disk plus the current run
    state. If the diagnostic has never been run, returns `result: null`.
    """
    return {
        "state": _diagnostic_state.get("status", "idle"),
        "error": _diagnostic_state.get("error"),
        "result": diagnostic.load_last_result(),
    }
