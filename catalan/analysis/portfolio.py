"""
Phase 4 — long-short portfolio construction and the headline statistics.

Two portfolios per day, from the LLM's scores over overnight announcements:

  initial-reaction portfolio  on init_ret  — the C0 comprehension check. NOT a
                              trading claim; you cannot trade before the news
                              prints. The paper's US hit rate was 93.3%.
  drift portfolio             on drift_ret — the C1/C2 tradable claim.

The short leg matters more than usual here: the paper finds it carries most of
the alpha (26 bps vs 8 bps daily). Because the drift leg is entirely intraday it
can be traded MIS, so the short leg IS executable on NSE — **except** in
trade-to-trade (T2T) and surveillance (ASM/GSM) names, where intraday shorting
is banned. Those are flagged per-day in the panel and excluded from the short
leg. They are not assumed away; a short-leg result computed without that filter
is not reportable.

Also reports the paper's §6.2 partial-rebalancing variant (25% rebalancing cut
turnover 190%→46%/day and *improved* net Sharpe at 10 bps).
"""
from __future__ import annotations

from typing import Optional


def daily_long_short(leg: str, rebalance_fraction: float = 1.0,
                     liquidity_bucket: Optional[int] = None):
    """Daily long-short returns for 'init' or 'drift'.

    Filtered to the PIT universe, to overnight announcements, and — on the
    short leg — to shortable names.
    """
    raise NotImplementedError("Phase 4")


def hit_rate(returns) -> float:
    """Share of days the long-short portfolio was positive. The C0 statistic."""
    raise NotImplementedError("Phase 4")


def sharpe(returns, periods_per_year: int = 252) -> float:
    """Annualised Sharpe. Report gross AND net — never gross alone."""
    raise NotImplementedError("Phase 4")


def deflated_sharpe(returns, n_trials: int) -> float:
    """Deflated Sharpe over the declared number of cells.

    ``n_trials`` is fixed in PREREGISTRATION.md before any result is seen.
    Passing the realised number of things tried would defeat the purpose.
    """
    raise NotImplementedError("Phase 4")
