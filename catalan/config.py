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

# Second line of defence, matched against the headline TEXT rather than `desc`.
#
# `desc` is vendor-assigned and there is no guarantee it stays consistent: a row
# whose text reads "... has informed the Exchange about Copy of Newspaper
# Publication" is the same non-event regardless of what category NSE filed it
# under. Measured on 2026-08-07 this catches zero rows the category filter
# misses (all 138 text mentions already carried the matching `desc`), so it is
# a guard against future drift, not a fix for a present leak — the kind of
# inconsistency that surfaces at row 50,000, not row 1,300.
#
# Case-insensitive substring patterns. Same status as CATEGORY_EXCLUSIONS:
# a declared study parameter, frozen before scoring.
HEADLINE_EXCLUSIONS = (
    "copy of newspaper publication",
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

# ADV is measured as RUPEE TURNOVER (close x volume), not share count.
# spread_impact_bps(bucket, participation) needs participation = order notional
# / ADV, and a share-count ADV cannot answer that: 10,000 shares of a Rs.50
# stock and 10,000 shares of a Rs.5,000 stock are not comparable liquidity.
ADV_MEASURE = "turnover_inr"

# Bucket ORIENTATION, declared. 1 = LEAST liquid ... 5 = MOST liquid.
# PREREGISTRATION reads its negative-result verdict off "the most illiquid
# bucket", so which integer that is has to be a study parameter rather than an
# implementation detail someone can flip while refactoring.
LIQUIDITY_BUCKET_1_IS_LEAST_LIQUID = True

# ── Returns panel construction ────────────────────────────────────────────────
# These are measurement rules, so they are frozen like every other parameter
# here: each one decides which rows enter C0/C1/C2, and changing one after
# scoring begins changes the sample the pre-registered thresholds were set for.

# Corporate-action guard. There is NO corporate-actions table anywhere in the
# repo and price_history OHLC is unadjusted (backfill_bhavcopy.py:33), so a
# split shows up as a fake overnight return. |init_ret| above this nulls
# init_ret AND prev_close, and retains open/close/drift_ret unchanged: on an
# ex-date all four prices are already quoted in the post-action basis, so only
# the comparison to the PREVIOUS close crosses the basis change. The tradable
# leg is untouched, which is why this guard costs the C2 ship gate nothing.
#
# 0.20 is defensible against NSE price bands (5/10/20%, 10% dynamic for F&O):
# a >20% overnight move that is not a corporate action is near-unobtainable,
# while a 1:2 split is -50% and a 1:10 split is -90%.
#
# DECLARED RESIDUAL: ex-dividends and rights issues are NOT caught — a 1-2%
# yield sits well inside the band — which imparts a small negative bias to
# init_ret on ex-dates. Stated, not silently corrected.
PANEL_CA_GAP_LIMIT = 0.20

# Phantom-session floor. A date in price_history carrying a handful of bars is
# a partial write, not a trading day, and admitting one shifts every symbol's
# prev_close by a session and becomes a date attribution_date() can return.
# Relative floor with an absolute backstop: a fixed constant right for 2026
# coverage would be wrong for 2019.
PANEL_MIN_SESSION_BREADTH_ABS = 20
PANEL_MIN_SESSION_BREADTH_REL = 0.25    # x median breadth over the window

# Weekly-cadence guard. Before the 2025-03-17 cutover, price_history held
# weekly AGGREGATED OHLC (av_weekly_backfill.py:20-22): `open` is the week's
# open and `close` the week's close, so BOTH legs are weekly returns wearing a
# session label. Detected per symbol on its own bars as a rolling median gap.
# 1.6 sessions follows the precedent at quant_engine short_horizon.py:78-80.
PANEL_CADENCE_WINDOW = 10
PANEL_CADENCE_MIN_PERIODS = 5
PANEL_CADENCE_MAX_MEDIAN_GAP = 1.6

# Warm-up, counted in SESSIONS and never calendar days — a calendar-day guess
# under-fetches across holiday clusters and ADV then silently returns NULL for
# the first week of every build. ADV needs 20 bars; the cadence median needs
# another CADENCE_WINDOW behind it.
PANEL_WARMUP_SESSIONS = ADV_WINDOW_DAYS + PANEL_CADENCE_WINDOW

# Turso has no server-side cursor and a fixed 30s HTTP timeout
# (turso_client.py:126-131). A quarter of price_history (~52k rows) times out;
# a month (~17k) does not.
PANEL_FETCH_CHUNK = "month"

# Paper §6.2: 25% partial rebalancing cut turnover 190%→46%/day and *improved*
# net Sharpe at 10 bps. Reported as a declared variant, not a rescue attempt.
REBALANCE_FRACTIONS = (1.0, 0.25)

# ── Pre-registration gates ────────────────────────────────────────────────────
# Thresholds live in PREREGISTRATION.md, which is the authority. These names
# exist so the API and UI can address the cells.
GATES = ("C0", "C1", "C2")
