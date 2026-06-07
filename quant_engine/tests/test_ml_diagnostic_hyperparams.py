"""
Assertion tests for P2-d — diagnostic loads deployed RF hyperparameters from
``ML_MODEL_DIR/metadata.json`` instead of measuring the static ``RF_PARAMS``
constant in ``trainer.py``.

The bug this prevents: ``_tune_hyperparams`` in production picks
``max_depth=12, min_samples_leaf=20`` for the live pickle, but ``RF_PARAMS``
still reads ``max_depth=8, min_samples_leaf=30``. Pre-P2-d the diagnostic
measured the latter, so the IC/Sharpe numbers in ``ml_diagnostic.json`` did
not characterise the model actually in production. These tests guarantee
the helper now reads the deployed hyperparams and falls back gracefully
when the metadata is missing / unreadable.

No DB; everything works on in-tmp_path JSON fixtures.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from quant_engine.ml import diagnostic


def _write_meta(tmp_path: Path, body: dict) -> Path:
    p = tmp_path / "metadata.json"
    with open(p, "w") as fh:
        json.dump(body, fh)
    return p


# ── Happy path: metadata.json present and well-formed ────────────────────────
def test_loads_deployed_rf_params_when_metadata_present(tmp_path):
    deployed = {
        "n_estimators": 300,
        "max_depth": 12,
        "min_samples_leaf": 20,
        "max_features": "sqrt",
        "class_weight": "balanced",
        "random_state": 42,
        "n_jobs": -1,
    }
    _write_meta(tmp_path, {"rf_params": deployed, "n_samples": 370139})

    fallback = {"n_estimators": 999, "max_depth": -1}  # would be obvious if used
    with patch.object(diagnostic, "ML_MODEL_DIR", tmp_path):
        params, source = diagnostic._load_deployed_rf_params(fallback)

    assert params == deployed, \
        f"P2-d: loader must return the metadata.json rf_params verbatim, got {params}"
    assert source == "metadata.json", \
        f"source tag must announce metadata.json was used, got {source!r}"
    # Mutating the returned dict must NOT corrupt subsequent calls (dict copy contract).
    params["max_depth"] = -999
    params2, _ = None, None
    with patch.object(diagnostic, "ML_MODEL_DIR", tmp_path):
        params2, _ = diagnostic._load_deployed_rf_params(fallback)
    assert params2["max_depth"] == 12, \
        "loader must return a fresh dict each call so callers can't corrupt the cache"


# ── Fallback path: metadata.json missing ─────────────────────────────────────
def test_falls_back_to_default_when_metadata_missing(tmp_path):
    fallback = {"n_estimators": 300, "max_depth": 8, "min_samples_leaf": 30}
    # tmp_path intentionally has no metadata.json
    with patch.object(diagnostic, "ML_MODEL_DIR", tmp_path):
        params, source = diagnostic._load_deployed_rf_params(fallback)
    assert params == fallback, \
        "missing metadata must fall back to the supplied default params"
    assert "metadata.json missing" in source, \
        f"source tag must announce metadata was missing, got {source!r}"


# ── Fallback path: malformed metadata (no rf_params key) ─────────────────────
def test_falls_back_when_rf_params_key_missing(tmp_path):
    _write_meta(tmp_path, {"n_samples": 1000})  # no rf_params at all
    fallback = {"max_depth": 8}
    with patch.object(diagnostic, "ML_MODEL_DIR", tmp_path):
        params, source = diagnostic._load_deployed_rf_params(fallback)
    assert params == fallback
    assert "parse error" in source or "missing" in source, \
        f"source tag must announce the parse failure, got {source!r}"


# ── Fallback path: rf_params present but wrong type ──────────────────────────
def test_falls_back_when_rf_params_not_a_dict(tmp_path):
    _write_meta(tmp_path, {"rf_params": "not a dict"})
    fallback = {"max_depth": 8}
    with patch.object(diagnostic, "ML_MODEL_DIR", tmp_path):
        params, source = diagnostic._load_deployed_rf_params(fallback)
    assert params == fallback
    assert "parse error" in source, \
        f"source tag must surface the parse failure, got {source!r}"


# ── Fallback path: corrupt JSON ──────────────────────────────────────────────
def test_falls_back_on_corrupt_json(tmp_path):
    p = tmp_path / "metadata.json"
    with open(p, "w") as fh:
        fh.write("{not json at all")
    fallback = {"max_depth": 8}
    with patch.object(diagnostic, "ML_MODEL_DIR", tmp_path):
        params, source = diagnostic._load_deployed_rf_params(fallback)
    assert params == fallback
    assert "parse error" in source, \
        f"corrupt JSON must surface as a parse error, got {source!r}"


# ── End-to-end: deployed params actually differ from the constant ────────────
def test_helper_surfaces_deployment_drift_against_trainer_constant():
    """If metadata.json on disk encodes hyperparams that differ from the
    static ``RF_PARAMS`` constant in ``trainer.py``, the helper must surface
    the *deployed* ones — proving the diagnostic is no longer married to
    the constant. We compare against the real metadata.json checked in to
    repo so the test fails the moment someone wires the legacy constant
    back in by accident."""
    from quant_engine.ml.trainer import RF_PARAMS
    params, source = diagnostic._load_deployed_rf_params(RF_PARAMS)
    # Tolerate either: (a) metadata.json present and deployed != constant
    # (drift correctly captured), or (b) deployed happens to equal constant
    # because a coincident retrain. What we DON'T tolerate is the fallback
    # branch firing silently when metadata.json IS on disk.
    meta_path = Path(diagnostic.ML_MODEL_DIR) / "metadata.json"
    if meta_path.exists():
        assert source == "metadata.json", (
            f"metadata.json exists at {meta_path} but loader fell back to "
            f"the constant — source={source!r}. This means the P2-d helper "
            f"is silently broken and the diagnostic is measuring the wrong model."
        )
