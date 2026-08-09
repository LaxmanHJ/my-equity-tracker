"""
Phase 4 — the memorisation test (paper Online Appendix C).

The paper's design rests entirely on scoring headlines released *after* the
model's training cutoff. Claude Opus 5's cutoff is May 2026 and today is past
it, so a 2019→2026 backtest scored by Opus 5 is a model recalling what happened,
not predicting it. This is the single largest threat to the study's validity —
larger than the cost problem, because it is invisible in the output.

The test: **holding the category mix fixed**, does a model score better on
pre-cutoff headlines than on post-cutoff ones? Holding the mix fixed matters —
the composition of NSE announcements drifts over time, so an unconditional
pre/post comparison confounds memorisation with topic drift.

A pre-cutoff advantage quantifies memorisation and **caps what the historical
arm may claim**. The forward log is unaffected by construction, which is why it
is the only ship gate.

The model-size arm doubles as a second read on this: models with different
cutoffs should show the contamination boundary in different places. If they
don't, the effect measured here isn't memorisation.
"""
from __future__ import annotations

from typing import Dict

# Verify each cutoff from the Anthropic docs before assigning a model to an arm
# (open item 1). Do not assume — an assumed cutoff invalidates this whole test.
MODEL_CUTOFFS: Dict[str, str] = {}


def pre_post_cutoff_comparison(model_id: str, stratify_by_category: bool = True):
    """Accuracy pre- vs post-cutoff, category mix held fixed."""
    raise NotImplementedError("Phase 4")


def contamination_ceiling(model_id: str) -> float:
    """How much of the historical arm's measured edge is attributable to recall.

    The number that caps what the historical arm is allowed to claim.
    """
    raise NotImplementedError("Phase 4")
