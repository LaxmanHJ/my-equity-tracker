"""
Pluggable text-to-score interface.

A scorer takes a string (headline + short body) and returns a float in
[-1.0, +1.0] together with a label-set ID so downstream code can record
which model produced the score.

Two scorers ship today:

  textblob_v1   — TextBlob polarity. Works out of the box (already in
                  requirements.txt). Coarse; misclassifies a lot of
                  finance-specific language ("beat estimates by 5%" can
                  read neutral; "downgrade" is correctly negative). Used
                  as the baseline so the pipeline produces signal even on
                  hosts without ML deps installed.

  finbert_v1    — FinBERT (yiyanghkust/finbert-tone or kdave/FineTuned_Finbert).
                  Lazy-imports transformers + torch; raises a clear error
                  if those aren't installed so the install step is obvious.
                  Target scorer for production — Indian-finance fine-tuned
                  variants beat vanilla FinBERT by ~15% F1 (see wiki).

Selection priority is settable per-call (`prefer=...`) or via the
SENTIMENT_SCORER env var. Default chain: ["finbert_v1", "textblob_v1"]
— FinBERT wins when available, falls back automatically.

Why an interface rather than two functions: the aggregator needs to write
`scorer_version` to Turso, and the swap from TextBlob → FinBERT shouldn't
require touching backfill / feature code.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SCORER_CHAIN = ("finbert_v1", "textblob_v1")


@dataclass(frozen=True)
class ScorerInfo:
    """Metadata about which scorer produced a score."""
    version: str
    label_set: str  # what the score range means semantically
    range: tuple[float, float]


SCORER_INFO = {
    "textblob_v1": ScorerInfo(
        version="textblob_v1",
        label_set="polarity",
        range=(-1.0, 1.0),
    ),
    "finbert_v1": ScorerInfo(
        version="finbert_v1",
        label_set="prob(pos) - prob(neg)",
        range=(-1.0, 1.0),
    ),
}


def _score_textblob(text: str) -> Optional[float]:
    """TextBlob polarity in [-1, 1]. None on empty/error."""
    if not text or not text.strip():
        return None
    try:
        from textblob import TextBlob
    except ImportError:
        logger.error("textblob not installed — `pip install -r quant_engine/requirements.txt`")
        return None
    try:
        return float(TextBlob(text).sentiment.polarity)
    except Exception as exc:  # noqa: BLE001 — TextBlob raises generic Exception
        logger.warning("textblob scoring failed: %s", exc)
        return None


_FINBERT_PIPELINE = None  # lazy-loaded singleton


def _score_finbert(text: str) -> Optional[float]:
    """
    FinBERT score in [-1, +1] as prob(positive) - prob(negative).

    Returns None if transformers/torch aren't installed — caller falls back
    to the next scorer in the chain. The model is loaded once per process.
    """
    if not text or not text.strip():
        return None
    global _FINBERT_PIPELINE
    if _FINBERT_PIPELINE is None:
        try:
            from transformers import pipeline  # type: ignore[import-not-found]
            model_name = os.getenv("FINBERT_MODEL", "yiyanghkust/finbert-tone")
            logger.info("Loading FinBERT model %s (one-time per process)", model_name)
            _FINBERT_PIPELINE = pipeline(
                "sentiment-analysis",
                model=model_name,
                top_k=None,  # return all label probabilities
            )
        except ImportError:
            logger.info("transformers not installed; FinBERT scorer unavailable")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("FinBERT load failed: %s", exc)
            return None
    try:
        # Truncate to model max length to avoid runtime warnings
        out = _FINBERT_PIPELINE(text[:512])
        # `out` is a list of {label, score} dicts (top_k=None → all labels)
        scores = {item["label"].lower(): float(item["score"]) for item in out[0]}
        pos = scores.get("positive", 0.0)
        neg = scores.get("negative", 0.0)
        return pos - neg
    except Exception as exc:  # noqa: BLE001
        logger.warning("FinBERT inference failed: %s", exc)
        return None


_SCORERS = {
    "textblob_v1": _score_textblob,
    "finbert_v1":  _score_finbert,
}


def available_scorers() -> list[str]:
    """Names of scorers whose dependencies are installed. Useful for diagnostics."""
    avail = []
    for name in _SCORERS:
        if name == "textblob_v1":
            try:
                import textblob  # noqa: F401
                avail.append(name)
            except ImportError:
                pass
        elif name == "finbert_v1":
            try:
                import transformers  # noqa: F401
                avail.append(name)
            except ImportError:
                pass
    return avail


def score_text(
    text: str,
    prefer: Optional[tuple[str, ...]] = None,
) -> tuple[Optional[float], Optional[ScorerInfo]]:
    """
    Score `text` and return (score, scorer_info).

    Tries scorers in priority order; first non-None result wins. Returns
    (None, None) only if every scorer in the chain failed/returned None
    — meaning every model in the project couldn't produce a score for
    this text. Callers should record that as "no signal" rather than "0".

    Args:
        text:   raw string (headline + first paragraph is fine; longer is
                truncated by individual scorers as needed).
        prefer: tuple of scorer names to try in order. Defaults to env var
                SENTIMENT_SCORER (single name) or DEFAULT_SCORER_CHAIN.

    Returns:
        (score in [-1, +1], ScorerInfo of the scorer that produced it).
    """
    if prefer is None:
        env = os.getenv("SENTIMENT_SCORER")
        prefer = (env,) if env else DEFAULT_SCORER_CHAIN

    for name in prefer:
        fn = _SCORERS.get(name)
        if fn is None:
            logger.warning("Unknown scorer requested: %s", name)
            continue
        s = fn(text)
        if s is not None:
            # Clip to declared range to defend against numeric drift
            lo, hi = SCORER_INFO[name].range
            return max(lo, min(hi, s)), SCORER_INFO[name]
    return None, None
