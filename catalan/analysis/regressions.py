"""
Phase 4 — panel regressions of returns on the LLM score.

Firm and date fixed effects, standard errors **double-clustered by firm and
date**. Single-clustered or plain OLS SEs will overstate significance badly on
a panel this shaped: announcements cluster in time (results season) and within
firm, and the paper's own inference rests on the double-clustered version.

Controls that must be in the specification:
  - an earnings-window indicator from Turso ``earnings_events`` (106,982 rows,
    2,613 symbols) — otherwise the drift is partly PEAD wearing a costume;
  - firm size / liquidity bucket, since the paper's effect concentrates in
    small caps;
  - the announcement category (`desc`) fixed effect, so a result is not just
    one topic dominating the mix.
"""
from __future__ import annotations


def drift_on_score(controls: bool = True):
    """Regress drift_ret on the LLM score with firm+date FE, double-clustered SEs."""
    raise NotImplementedError("Phase 4")


def initial_reaction_on_score(controls: bool = True):
    """Same specification on init_ret — the comprehension side of the result."""
    raise NotImplementedError("Phase 4")
