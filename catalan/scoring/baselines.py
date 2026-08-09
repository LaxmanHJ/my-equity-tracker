"""
Non-LLM baselines — the control arm.

The paper's central claim is that financial reasoning over headlines is an
*emergent* capacity: bigger models score better, and dictionary methods do not
work at all. Reproducing that ordering on Indian text is both a replication of
§7 and a sanity check on our own pipeline — if Loughran-McDonald matches Opus 5
here, something upstream is broken.

Three baselines, all over the identical corpus:

  loughran_mcdonald — finance-specific sentiment word lists. Needs the LM
                      master dictionary; note it is US-English and Indian
                      filing language differs, which is itself worth reporting.
  textblob_v1       — already implemented in quant_engine/sentiment/scorer.py
  finbert_v1        — already implemented in quant_engine/sentiment/scorer.py

The latter two are reused, not reimplemented; CATALAN calls them through this
module so the provenance stamping stays uniform with the LLM arms.
"""
from __future__ import annotations

from typing import List, Optional


def score_loughran_mcdonald(headlines: List[str]) -> List[Optional[int]]:
    """Net positive/negative word count, signed to {-1, 0, +1}."""
    raise NotImplementedError("Phase 4 — model-size arm")


def score_repo_baseline(headlines: List[str], scorer: str) -> List[Optional[int]]:
    """Delegate to quant_engine.sentiment.scorer.score_batch for textblob/finbert.

    That function returns continuous scores with per-text fall-through; discretise
    to {-1, 0, +1} here so every arm lands on the same scale as the LLM arms.
    """
    raise NotImplementedError("Phase 4 — model-size arm")
