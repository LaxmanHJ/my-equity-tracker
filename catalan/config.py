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

STUDY_VERSION = "catalan-v2"   # 2026-08-07 → 10-25 is the declared pilot

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

# catalan-v2 study universe (Amendment 1): NSE's own NIFTY500 constituent list
# as published on 2026-10-18 (niftyindices.com ind_nifty500list.csv), frozen
# for the whole counted period. Fixed before any counted announcement, so it
# carries no look-ahead. The Turso roster could not be used: it predates the
# September 2026 reconstitution (~28 changes were never loaded).
#
# Only EQ-series shares count. BE (trade-for-trade) shares cannot be bought and
# sold intraday, and RR rows are REIT units, not company shares. Also excluded:
# DUMMYHEG (an index placeholder from the HEG demerger) and three stocks whose
# stored price history Angel back-adjusted after it was written, so new bars
# would create a false return (TRENT, CUB, and HEG, now traded as HEGAM).
UNIVERSE_FILE = "data/universe/nifty500_2026-10-18.csv"
UNIVERSE_FILE_SHA256 = "2959bf206239284e145f7aecc65095b18d11556d2642323a70f2d26efe0f5cb3"
UNIVERSE_SERIES = ("EQ",)
UNIVERSE_EXCLUDED = ("DUMMYHEG", "TRENT", "CUB", "HEGAM")

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

# Duplicate filings: same symbol, same IST day, same category, and filing text
# (the headline with the filer's name and the "has informed the Exchange" stem
# removed) at least this similar — unless NSE reports different attachment
# sizes, which means different documents. Keep the earliest, drop the rest.
# Applied at QUERY time, never at ingest.
#
# 1.0 = IDENTICAL filing text. The verifier's 2026-10-18 review found that 0.9
# still dropped distinct events whose text differs only in a date, a time or a
# name ("meet at 10.30 AM" vs "at 3.30 PM"; Rajendra vs Vipul Patel as MD). The
# portfolio is formed per stock per session, so a duplicate that survives costs
# almost nothing, while a distinct filing that is dropped loses information.
#
# Was 0.6 on the whole headline, following the paper. The 2026-09-27 data audit
# measured that rule on NSE text: it dropped 13.3% of the corpus, 63% of drops
# differed in category from their keeper, and 18 of 22 hand-read drops were
# distinct events. NSE's shared "<Company> has informed the Exchange ..."
# template made unrelated filings look alike. Changed by the 2026-09 amendment,
# before the confirmatory clock started.
DEDUP_SIMILARITY = 1.0

# The RavenPack-substitute. The paper required relevance=100 and dropped
# stock-gain/stock-loss categories; NSE hands us a 105–122 value vendor
# taxonomy in `desc`, which is better than clustering embeddings.
#
# NOT YET FROZEN — open item 3. These three are the obvious excludes from the
# probed June-2026 distribution; the final list must be decided against the
# full category distribution and committed before any scoring run.
CATEGORY_EXCLUSIONS_FROZEN = True    # frozen by Amendment 1 (catalan-v2), 2026-09-29
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

# ── PDF attachment text ───────────────────────────────────────────────────────
# A headline is often only the filing's label ("has submitted financial
# results"); the substance is in the attached PDF. Sized on a 145-filing random
# sample, 2026-09-27: page 1 is a covering letter to the exchange in 98% of
# filings, results and press-release substance starts on page 2, and the first
# three pages are ~900 text tokens at the median (p90 ~1,800).
#
# These rules decide which rows carry PDF text, so they are study parameters,
# not tuning knobs. Any page in the window that fails a readability test sends
# the whole filing to headline-only scoring, flagged — never partial text
# (decided 2026-09-27; ~11% of the sample).
PDF_PAGE_WINDOW = 3            # read pages 1..N only
PDF_MIN_PAGE_CHARS = 50        # fewer extracted characters = no text layer (a scan)
PDF_GARBLE_MAX = 0.15          # non-ASCII share of letters above this = broken font map
# Non-ASCII characters that are legitimate in Indian filings and never count as
# garble: Devanagari (bilingual PSU letterheads — RITES, BHEL) plus currency
# and typographic punctuation. Anything else non-ASCII does. Regex class body.
PDF_GARBLE_ALLOWED = "\u0900-\u097F₹–—’‘“”•●▪…©®°±×"
PDF_MAX_CHARS = 30_000         # ceiling on extracted text (~7.5k tokens); flagged if hit

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
# catalan-v2 primary model (owner's choice, 2026-10-18): Google's
# gemini-3.5-flash-lite on the free tier. Checked that day: the served
# modelVersion equals the requested model (no rerouting), it uses no thinking
# tokens, it accepts temperature 0, and it gave 3/3 identical verdicts on each
# of three test headlines. Google lists no shutdown date for it. The scorer
# halts if Google ever serves a different model under this name.
PRIMARY_MODEL = os.getenv("CATALAN_MODEL", "gemini-3.5-flash-lite")

# Sampling temperature, sent to every model that accepts it. The paper used 0.
# Until 2026-09 CATALAN sent none, so Haiku ran at its default of 1.0, and the
# data audit found 42% of headlines re-scored ~15x changed verdict between runs
# (222 got both YES and NO for identical input). Newer models (Opus 5 and
# later) reject the parameter with a 400; for those it is omitted and recorded
# as NULL. Every score row stores the temperature it was produced at.
TEMPERATURE = 0.0
TEMPERATURE_MODEL_PREFIXES = ("claude-haiku-4-5", "gemini-3.5-flash-lite")

# First announcement day counted by the amended pre-registration. Everything
# earlier (2026-08-07 → 2026-10-25) is the declared PILOT: usable for engineering and
# the pipeline check, never for the C1/C2 verdict. PDF text is extracted by the
# daily pipeline from this day on.
STUDY_CLOCK_START = "2026-10-26"

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
# Long-only v2 (Amendment 1): daily allowance for neutralising market drift with
# index futures, charged in C2 on top of the basket's round-trip cost.
HEDGE_COST_BPS = 1.0
# C2's cost model, fully specified before any return is seen (verifier D-S4):
# every basket position is POSITION_NOTIONAL_INR, so explicit costs follow the
# rates above at that size; the bid-ask spread is the Abdi-Ranaldo (2017)
# close-high-low estimator over the previous SPREAD_WINDOW_SESSIONS sessions,
# charged in full per round trip (half on entry, half on exit).
POSITION_NOTIONAL_INR = 100_000
# A counted session is usable only if at least this share of the study universe
# has a valid panel row (drift_ret) on it (Amendment 1, "usable counted session").
USABLE_SESSION_MIN_COVERAGE = 0.80
SPREAD_ESTIMATOR = "abdi_ranaldo_2017"
SPREAD_WINDOW_SESSIONS = 20
SPREAD_MIN_PAIRS = 15            # fewer usable consecutive-session pairs → stock-session not valid

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
