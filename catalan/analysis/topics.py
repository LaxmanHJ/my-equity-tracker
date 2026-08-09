"""
Phase 4 — per-category decomposition (the paper's Table 4).

NSE hands us a 105-122 value vendor topic taxonomy in ``desc``, which is a
better instrument than the embedding clusters the paper had to build. This
decomposes the drift result by category and asks the question that decides
whether the result is real: **is the edge spread across topics, or is it one
category doing all the work?**

A result driven by a single `desc` value is a narrow, fragile finding and must
be reported as one — not as "LLM reads Indian news".

Reports the count of announcements per category alongside every estimate; a
large per-category effect on 40 observations is noise.
"""
from __future__ import annotations


def decompose_by_category(leg: str = "drift", min_observations: int = 100):
    """Drift statistics per `desc`, with observation counts attached."""
    raise NotImplementedError("Phase 4")


def category_distribution():
    """Full `desc` frequency table over the ingested corpus.

    Also closes open item 3: the frozen exclusion list must be chosen against
    this full distribution, not the probed June-2026 top-12.
    """
    raise NotImplementedError("Phase 4")
