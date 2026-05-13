"""
Pluggable text-to-score interface.

A scorer takes a string (headline + short body) and returns a float in
[-1.0, +1.0] together with a label-set ID so downstream code can record
which model produced the score.

Three scorers ship today:

  claude_v1    — Calls claude-haiku-4-5 via the anthropic SDK. PRIMARY
                 production scorer. Best quality on finance text;
                 ~$0.0003 per headline (≈ $0.50/month at portfolio scale).
                 Requires ANTHROPIC_API_KEY in .env; abstains cleanly if
                 missing so the chain falls through.

  textblob_v1  — TextBlob polarity. Works out of the box (already in
                 requirements.txt). Coarse; misclassifies a lot of
                 finance-specific language ("beat estimates by 5%" can
                 read neutral). Baseline fallback so the pipeline
                 produces signal even without API access.

  finbert_v1   — FinBERT (yiyanghkust/finbert-tone or kdave/FineTuned_Finbert).
                 Lazy-imports transformers + torch (~1.5 GB install).
                 Optional alternative to Claude when you want local
                 inference; not in the default chain to keep the install
                 footprint small.

Selection priority is settable per-call (`prefer=...`) or via the
SENTIMENT_SCORER env var. Default chain: ["claude_v1", "textblob_v1"]
— Claude wins when ANTHROPIC_API_KEY is set, falls back automatically
to TextBlob otherwise.

Why an interface rather than three functions: the aggregator needs to
write `scorer_version` to Turso, and swapping scorers shouldn't require
touching backfill / feature code.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SCORER_CHAIN = ("claude_v1", "textblob_v1")

# Use the cheapest current Claude model for sentiment. Opus is overkill for
# headline classification and would cost ~30x more per call.
CLAUDE_MODEL = os.getenv("CLAUDE_SENTIMENT_MODEL", "claude-haiku-4-5-20251001")


@dataclass(frozen=True)
class ScorerInfo:
    """Metadata about which scorer produced a score."""
    version: str
    label_set: str  # what the score range means semantically
    range: tuple[float, float]


SCORER_INFO = {
    "claude_v1": ScorerInfo(
        version=f"claude_v1:{CLAUDE_MODEL}",
        label_set="sentiment_score",
        range=(-1.0, 1.0),
    ),
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


_CLAUDE_CLIENT = None  # lazy-loaded singleton

_CLAUDE_PROMPT = (
    "You are a financial-news sentiment classifier for Indian equities. "
    "Read the headline and return ONLY a JSON object of the form "
    '{"score": <number between -1 and 1>} where -1 = strongly negative '
    "for the stock price, +1 = strongly positive, 0 = neutral or "
    "non-price-relevant. Consider: earnings beats/misses, guidance, "
    "regulatory action, management change, M&A, downgrades, fraud, "
    "macro-level company impact. Do not include any other text."
)


def _score_claude(text: str) -> Optional[float]:
    """
    Score a headline via claude-haiku-4-5. Returns None when:
      * ANTHROPIC_API_KEY is missing (chain falls through to TextBlob)
      * the anthropic SDK isn't installed
      * the model returned an unparseable response (logged warning)
      * any transport-level failure (logged warning, chain falls through)

    Cost: ≈ 200 input + 15 output tokens per call → ≈ $0.0003 per headline
    on Haiku 4.5 pricing as of 2026-05.
    """
    if not text or not text.strip():
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None

    global _CLAUDE_CLIENT
    if _CLAUDE_CLIENT is None:
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError:
            logger.info("anthropic SDK not installed; claude_v1 scorer unavailable")
            return None
        try:
            _CLAUDE_CLIENT = anthropic.Anthropic()
        except Exception as exc:  # noqa: BLE001
            logger.warning("anthropic client init failed: %s", exc)
            return None

    try:
        resp = _CLAUDE_CLIENT.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=30,
            system=_CLAUDE_PROMPT,
            messages=[{"role": "user", "content": text[:600]}],
        )
    except Exception as exc:  # noqa: BLE001 — third-party transport
        logger.warning("Claude scorer transport failed: %s", exc)
        return None

    # Concatenate any text blocks in the reply (Claude can return >1 block)
    raw = "".join(
        getattr(b, "text", "") for b in (resp.content or [])
    ).strip()
    if not raw:
        return None
    return _extract_score_from_claude_text(raw)


def _extract_score_from_claude_text(raw: str) -> Optional[float]:
    """
    Pull a numeric score out of Claude's reply.

    Preferred path: parse as JSON `{"score": <num>}`. Fallback: regex for
    the first signed decimal in [-1, 1]. Returns None if neither yields a
    valid number in range — caller treats as abstain.
    """
    # JSON path
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "score" in obj:
            v = float(obj["score"])
            if -1.0 <= v <= 1.0:
                return v
    except (ValueError, TypeError):
        pass

    # Fenced JSON block (sometimes the model wraps it)
    m = re.search(r'\{[^{}]*"score"\s*:\s*(-?\d*\.?\d+)[^{}]*\}', raw)
    if m:
        try:
            v = float(m.group(1))
            if -1.0 <= v <= 1.0:
                return v
        except ValueError:
            pass

    # Last resort: first signed decimal in the reply
    m = re.search(r'-?\d*\.?\d+', raw)
    if m:
        try:
            v = float(m.group(0))
            if -1.0 <= v <= 1.0:
                return v
        except ValueError:
            pass

    logger.warning("Claude returned unparseable sentiment reply: %r", raw[:200])
    return None


_SCORERS = {
    "claude_v1":   _score_claude,
    "textblob_v1": _score_textblob,
    "finbert_v1":  _score_finbert,
}


def available_scorers() -> list[str]:
    """Names of scorers whose dependencies AND credentials are available.

    A scorer is "available" only if a call would *plausibly* produce a
    score — for Claude this means both the SDK is installed and the API
    key is set. Useful for diagnostics / dashboard health checks.
    """
    avail = []
    for name in _SCORERS:
        if name == "claude_v1":
            if not os.getenv("ANTHROPIC_API_KEY"):
                continue
            try:
                import anthropic  # noqa: F401
                avail.append(name)
            except ImportError:
                pass
        elif name == "textblob_v1":
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
