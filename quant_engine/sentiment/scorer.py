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

# How many headlines to send per Claude API call. With Haiku 4.5's 200K context
# this is bounded by output, not input: each item produces ~25 output tokens,
# and we cap max_tokens around 50*30 = 1500 to keep latency low and stay
# comfortably under the API ceiling.
_CLAUDE_BATCH_SIZE = int(os.getenv("CLAUDE_SENTIMENT_BATCH_SIZE", "30"))

_CLAUDE_PROMPT = (
    "You are a financial-news sentiment classifier for Indian equities. "
    "Read the headline and return ONLY a JSON object of the form "
    '{"score": <number between -1 and 1>} where -1 = strongly negative '
    "for the stock price, +1 = strongly positive, 0 = neutral or "
    "non-price-relevant. Consider: earnings beats/misses, guidance, "
    "regulatory action, management change, M&A, downgrades, fraud, "
    "macro-level company impact. Do not include any other text."
)

_CLAUDE_BATCH_PROMPT = (
    "You are a financial-news sentiment classifier for Indian equities. "
    "You will receive a JSON object of the form "
    '{"items": [{"i": <int>, "text": <headline>}, ...]}. '
    "For EACH item, return a sentiment score in [-1.0, +1.0] where "
    "-1 = strongly negative for the stock price, +1 = strongly positive, "
    "0 = neutral or non-price-relevant. Consider: earnings beats/misses, "
    "guidance, regulatory action, management change, M&A, downgrades, "
    "fraud, macro-level company impact. "
    'Reply with ONLY a JSON object: {"scores": [{"i": <int>, "score": <num>}, ...]} '
    "containing exactly one entry per input item. No prose, no code fences."
)


def _ensure_claude_client():
    """Lazy-initialise the Anthropic client singleton. Returns None on failure."""
    global _CLAUDE_CLIENT
    if _CLAUDE_CLIENT is not None:
        return _CLAUDE_CLIENT
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
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
    return _CLAUDE_CLIENT


def _log_anthropic_error(context: str, exc: BaseException) -> None:
    """Log full Anthropic API error including status code and response body.

    The bare exception message often hides the actual cause ("Method Not
    Allowed" vs "model not found" vs "invalid_request_error: field X"); the
    response payload is where the real detail lives.
    """
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "response", None)
    body_text = None
    if body is not None:
        try:
            body_text = body.text  # httpx.Response
        except Exception:  # noqa: BLE001
            body_text = repr(body)
    logger.warning(
        "Claude %s failed: %s [%s] status=%s body=%s",
        context, type(exc).__name__, exc, status, body_text,
    )


def _score_claude(text: str) -> Optional[float]:
    """
    Single-text Claude scorer — thin wrapper around the batch path so all
    Anthropic traffic flows through one code path. Returns None on any of:
      * empty text
      * missing ANTHROPIC_API_KEY (chain falls through to TextBlob)
      * anthropic SDK not installed
      * unparseable response
      * transport failure (logged with full Anthropic error body)
    """
    if not text or not text.strip():
        return None
    results = _batch_score_claude([text])
    return results[0] if results else None


def _batch_score_claude(texts: list[str]) -> list[Optional[float]]:
    """
    Score N texts via Claude in batched API calls.

    Bundles up to _CLAUDE_BATCH_SIZE headlines per `messages.create` call,
    cutting per-symbol traffic from O(articles) to O(ceil(articles / batch)).
    Returns one Optional[float] per input in the same order; None indicates
    "Claude abstained / failed for this entry" so callers can fall through
    to the next scorer in the chain.
    """
    if not texts:
        return []
    client = _ensure_claude_client()
    if client is None:
        return [None] * len(texts)

    out: list[Optional[float]] = [None] * len(texts)

    for start in range(0, len(texts), _CLAUDE_BATCH_SIZE):
        chunk = texts[start:start + _CLAUDE_BATCH_SIZE]
        # Drop empty entries up-front so we don't waste API budget on them.
        items = []
        local_to_global: list[int] = []
        for j, t in enumerate(chunk):
            if t and t.strip():
                items.append({"i": len(items), "text": t[:600]})
                local_to_global.append(start + j)
        if not items:
            continue

        payload = json.dumps({"items": items}, ensure_ascii=False)
        try:
            resp = client.messages.create(
                model=CLAUDE_MODEL,
                # ~25 output tokens per item + JSON framing; cap generously.
                max_tokens=min(4000, 50 * len(items) + 50),
                system=_CLAUDE_BATCH_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
        except Exception as exc:  # noqa: BLE001 — third-party transport
            _log_anthropic_error(f"batch (n={len(items)})", exc)
            continue  # leave this chunk's entries as None

        raw = "".join(
            getattr(b, "text", "") for b in (resp.content or [])
        ).strip()
        if not raw:
            continue
        parsed = _extract_batch_scores_from_claude_text(raw, n_expected=len(items))
        for local_i, score in enumerate(parsed):
            if score is not None:
                out[local_to_global[local_i]] = score

    return out


def _extract_batch_scores_from_claude_text(
    raw: str, n_expected: int,
) -> list[Optional[float]]:
    """Parse Claude's batched reply into n_expected scores (in local-index order).

    Expected reply: {"scores": [{"i": <int>, "score": <num>}, ...]}. Tolerates
    prose around the JSON object (model occasionally adds it despite the
    instruction). Returns a list of length n_expected, None for any
    missing/invalid entries — caller treats those as abstain.
    """
    out: list[Optional[float]] = [None] * n_expected

    obj = None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        m = re.search(r'\{.*"scores"\s*:.*\}', raw, flags=re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
            except (ValueError, TypeError):
                obj = None

    if not isinstance(obj, dict):
        logger.warning("Claude batch reply unparseable: %r", raw[:300])
        return out

    scores = obj.get("scores")
    if not isinstance(scores, list):
        logger.warning("Claude batch reply missing 'scores' array: %r", raw[:300])
        return out

    for entry in scores:
        if not isinstance(entry, dict):
            continue
        i = entry.get("i")
        s = entry.get("score")
        if not isinstance(i, int) or i < 0 or i >= n_expected:
            continue
        try:
            v = float(s)
        except (TypeError, ValueError):
            continue
        if -1.0 <= v <= 1.0:
            out[i] = v
    return out


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


def _batch_score_textblob(texts: list[str]) -> list[Optional[float]]:
    """TextBlob is CPU-local — a plain loop is identical in cost to "batching"
    but the wrapper keeps the scorer-chain plumbing uniform."""
    return [_score_textblob(t) for t in texts]


def _batch_score_finbert(texts: list[str]) -> list[Optional[float]]:
    """Could be vectorised via the transformers pipeline, but FinBERT is
    optional and rarely the active scorer — a loop keeps the surface minimal."""
    return [_score_finbert(t) for t in texts]


_BATCH_SCORERS = {
    "claude_v1":   _batch_score_claude,
    "textblob_v1": _batch_score_textblob,
    "finbert_v1":  _batch_score_finbert,
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


def score_batch(
    texts: list[str],
    prefer: Optional[tuple[str, ...]] = None,
) -> list[tuple[Optional[float], Optional[ScorerInfo]]]:
    """
    Score N texts and return N (score, scorer_info) pairs in input order.

    Same chain semantics as score_text(): tries each scorer in priority
    order; texts where a scorer returns None fall through to the next
    scorer in the chain. The Claude scorer batches up to
    CLAUDE_SENTIMENT_BATCH_SIZE texts per API call — for a 30-headline
    symbol that's 1 request instead of 30, with the same fallback to
    TextBlob for any per-text abstains.

    Returns (None, None) entries for texts where every scorer failed.
    """
    if not texts:
        return []
    if prefer is None:
        env = os.getenv("SENTIMENT_SCORER")
        prefer = (env,) if env else DEFAULT_SCORER_CHAIN

    n = len(texts)
    out: list[tuple[Optional[float], Optional[ScorerInfo]]] = [(None, None)] * n
    pending = list(range(n))

    for name in prefer:
        if not pending:
            break
        batch_fn = _BATCH_SCORERS.get(name)
        if batch_fn is None:
            logger.warning("Unknown scorer requested: %s", name)
            continue
        sub_texts = [texts[i] for i in pending]
        sub_scores = batch_fn(sub_texts)
        info = SCORER_INFO[name]
        lo, hi = info.range
        next_pending: list[int] = []
        for idx, score in zip(pending, sub_scores):
            if score is None:
                next_pending.append(idx)
                continue
            out[idx] = (max(lo, min(hi, score)), info)
        pending = next_pending

    return out


def score_text(
    text: str,
    prefer: Optional[tuple[str, ...]] = None,
) -> tuple[Optional[float], Optional[ScorerInfo]]:
    """
    Single-text wrapper around score_batch — kept for backward compatibility
    and for callers that have only one string to score. New code paths that
    score multiple texts should call score_batch directly to benefit from
    the per-call batching in the Claude scorer.
    """
    results = score_batch([text], prefer=prefer)
    return results[0] if results else (None, None)
