"""
CATALAN — frozen study constants.

Everything in this module is a **study parameter**, not a tunable. Changing any
value here after scoring has begun invalidates the run: the pre-registration in
``PREREGISTRATION.md`` is written against these numbers, and score rows carry a
prompt hash but not a config hash.

If a constant genuinely needs to change mid-study, bump ``STUDY_VERSION`` and
treat the result as a separate study.
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

# CATALAN reads the main repo's .env for Turso + Anthropic credentials.
# It never writes to it and never adds keys of its own.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STUDY_VERSION = "catalan-v1"

# ── Service ───────────────────────────────────────────────────────────────────
# Own port. 3000 is Node, 5001 is the quant engine — CATALAN touches neither.
HOST = "0.0.0.0"
PORT = 5100

CATALAN_ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CATALAN_DB_PATH", CATALAN_ROOT / "data" / "catalan.db"))
UI_DIR = CATALAN_ROOT / "ui"

# ── Universe ──────────────────────────────────────────────────────────────────
# Point-in-time NIFTY500 via quant_engine's MembershipRegistry (837 ever-members,
# 940 spells, 2018-01-01 → 2026-07-16). PIT is non-negotiable: today's roster
# silently excludes exactly the stocks that were removed because they failed.
UNIVERSE_INDEX = "NIFTY500"

# ── NSE session clock (IST, and IST has no DST) ───────────────────────────────
# The paper's US definitions do not transfer unmodified. NSE runs a pre-open
# call auction 09:00–09:15, so the daily `open` is an auction clearing price —
# the clean analogue of the paper's opening auction.
MARKET_CLOSE = time(15, 30)   # previous session's close
NEWS_CUTOFF = time(9, 0)      # news after this is NOT tradable at today's open
MARKET_OPEN = time(9, 15)     # pre-open auction clears

# An announcement is "overnight" for session t if it landed after MARKET_CLOSE
# on t-1 or before NEWS_CUTOFF on t. The 15-minute gap between NEWS_CUTOFF and
# MARKET_OPEN mirrors the buffer the paper leaves before the US open.

# ── Return legs ───────────────────────────────────────────────────────────────
# init_ret  = close(t-1) → open(t).  The market's immediate reaction. NOT tradable.
# drift_ret = open(t)    → close(t). The tradable leg — this is the whole study.
#
# NSE's close is a VWAP of 15:00–15:30, not a call auction. The paper justified
# flat-bps costs because both its legs cleared in auctions with no bid-ask
# spread. That justification does not hold on our exit: the exit pays real
# spread. See analysis/costs.py.

# ── Corpus ────────────────────────────────────────────────────────────────────
NSE_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"
NSE_WARMUP_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"

HISTORY_START = "2019-01-01"   # probed reachable; open item 2 confirms no gap years
INGEST_CHUNK_DAYS = 90         # a 3-month window returns in a single request

# Near-duplicate headlines: same symbol, same day, Damerau-Levenshtein
# similarity above this threshold → keep the earliest, drop the rest.
# Follows the paper. Applied at QUERY time, never at ingest.
DEDUP_SIMILARITY = 0.6

# The RavenPack-substitute. The paper required relevance=100 and dropped
# stock-gain/stock-loss categories; NSE hands us a 105–122 value vendor
# taxonomy in `desc`, which is better than clustering embeddings.
#
# NOT YET FROZEN — open item 3. These three are the obvious excludes from the
# probed June-2026 distribution; the final list must be decided against the
# full category distribution and committed before any scoring run.
CATEGORY_EXCLUSIONS_FROZEN = False
CATEGORY_EXCLUSIONS = (
    "Trading Window",
    "Copy of Newspaper Publication",
    "Disclosure under SEBI Takeover Regulations",
)

# ── Model arms ────────────────────────────────────────────────────────────────
# Replicates the paper's §7 "financial reasoning is an emergent capacity" claim
# and doubles as the contamination control: a smaller model has a different
# training cutoff, so divergence between arms is informative.
#
# Opus 5's cutoff is May 2026 and today is past it — a 2019→2026 backtest scored
# by Opus 5 is partly recall, not prediction. That is exactly why the forward
# log is the only ship gate. Verify each cutoff from Anthropic docs before
# assigning a model to an arm (open item 1) — do not assume.
MODEL_ARMS = (
    "claude-haiku-4-5-20251001",
    "claude-sonnet-5",
    "claude-opus-5",
)
PRIMARY_MODEL = os.getenv("CATALAN_MODEL", "claude-haiku-4-5-20251001")

# Non-LLM baselines, for the same corpus.
BASELINE_SCORERS = ("loughran_mcdonald", "textblob_v1", "finbert_v1")

# Batch API: 50% discount, prompt caching on the frozen system prefix.
SCORING_BATCH_SIZE = 100
SCORE_VALUES = {"YES": 1, "UNKNOWN": 0, "NO": -1}

# ── Cost model — declared NOW, not fitted later ───────────────────────────────
# Zerodha MIS intraday round trip. R2 passed its gate gross of costs and then
# died on costs; declaring the bar first is what stops that repeating.
BROKERAGE_PER_ORDER_CAP = 20.0      # ₹ per order
BROKERAGE_RATE = 0.0003             # 0.03%, whichever is lower
STT_SELL_RATE = 0.00025             # 0.025%, sell side only (intraday)
EXCHANGE_TXN_RATE = 0.0000297       # ~0.00297%, both sides
STAMP_DUTY_BUY_RATE = 0.00003       # 0.003%, buy side only
SEBI_TURNOVER_RATE = 0.000001       # ₹10 per crore
GST_RATE = 0.18                     # on (brokerage + exchange txn)

# The explicit components land around 4–6 bps round trip at meaningful size.
# THE EXPLICIT PART IS NOT THE RISK — spread and impact are. The paper's
# strategy dies at 20 bps and its turnover was ~190%/day. So spread/impact is
# estimated PER LIQUIDITY BUCKET and never as one flat cross-sectional number.
LIQUIDITY_BUCKETS = 5               # by 20-day ADV, within-date quintiles
ADV_WINDOW_DAYS = 20

# Paper §6.2: 25% partial rebalancing cut turnover 190%→46%/day and *improved*
# net Sharpe at 10 bps. Reported as a declared variant, not a rescue attempt.
REBALANCE_FRACTIONS = (1.0, 0.25)

# ── Pre-registration gates ────────────────────────────────────────────────────
# Thresholds live in PREREGISTRATION.md, which is the authority. These names
# exist so the API and UI can address the cells.
GATES = ("C0", "C1", "C2")
