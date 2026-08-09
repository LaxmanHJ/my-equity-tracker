"""
The Indian cost model — declared before the result is seen, not fitted after.

This module exists because of what happened to R2: it produced a gate PASS
gross of costs and then died on costs. The rates below come from config.py and
are frozen; nothing here may be re-parameterised once C1 has been computed.

Zerodha MIS intraday round trip, per leg-pair:

    brokerage      ₹20/order or 0.03%, whichever lower, × 2 orders
    STT            0.025% on the sell side (intraday)
    exchange txn   ~0.00297% × 2
    stamp duty     0.003% buy side
    SEBI turnover  ₹10 per crore
    GST            18% on (brokerage + exchange txn)

That lands around **4-6 bps round trip** at meaningful order size.

**The explicit part is not the risk — spread and impact are.** The paper's
strategy dies at 20 bps and its turnover was ~190%/day, and our exit is worse
than the paper's: NSE's close is a VWAP of 15:00-15:30, not a call auction, so
the exit pays real spread. The paper explicitly justified flat-bps costs on the
grounds that both its legs cleared in auctions with no bid-ask spread. That
justification does not carry here.

Consequences, all declared now:
  - spread/impact is estimated PER LIQUIDITY BUCKET from price_history +
    traded volume, never as one flat cross-sectional number;
  - results are reported per bucket, because the paper's alpha concentrates in
    small caps — exactly where impact is worst;
  - **if the edge survives only in the most illiquid bucket, that is a negative
    result**, and this file says so before anyone has seen the number.
"""
from __future__ import annotations

from typing import Optional


def explicit_cost_bps(notional: float, side: str = "roundtrip") -> float:
    """Statutory + brokerage costs in basis points for a given order notional.

    Size-dependent because the ₹20 brokerage cap binds at ~₹66,667 notional.
    """
    raise NotImplementedError("Phase 4 — rates are frozen in config.py")


def spread_impact_bps(liquidity_bucket: int, participation: float) -> float:
    """Estimated spread + market impact for a bucket, in bps.

    Estimated from price_history and traded volume per bucket. Deliberately
    separate from explicit_cost_bps so the two can be reported apart — they
    have very different error bars and the reader should see which one kills
    the strategy if one does.
    """
    raise NotImplementedError("Phase 4")


def total_cost_bps(liquidity_bucket: int, notional: float,
                   participation: float) -> float:
    """explicit + spread/impact. This is what C2 nets out."""
    raise NotImplementedError("Phase 4")
