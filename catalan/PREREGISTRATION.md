# CATALAN — Pre-registration

> **STATUS: AMENDED 2026-10-18 — study version catalan-v2. Amendment 1
> (directly below) supersedes the v1 text wherever they conflict.**
> v1 status: thresholds set 2026-08-20.
>
> **Sequencing, stated plainly because it is the whole point.** Scoring began
> before this file was finalised: 7,884 headlines carried a verdict when these
> numbers were fixed. That is a deviation from the original plan and it is
> recorded rather than hidden.
>
> **It does not compromise the pre-registration, for a specific and checkable
> reason: not one gate statistic had been computable.** Turso `price_history`
> ends 2026-08-04 and the forward log begins 2026-08-07 — the two do not
> overlap, so no scored headline had ever been joined to a return. No hit rate,
> no drift, no Sharpe existed to be seen. The thresholds below were therefore
> set blind to every quantity they judge, which is the only property that
> matters.
>
> Anyone auditing this can verify it from git history: the panel builder
> (`catalan/data/panel.py`) landed 2026-08-19 and the price-data gap is recorded
> in `catalan/README.md` under "Known data limits" from the same commit.

This is the single most important sequencing decision in the project. R2
produced a gate PASS gross of costs and then died on costs. Declaring the bar
before seeing the number is what prevents that repeating — and the only way
that works is if the declaration is dated earlier than the result.

---

## AMENDMENT 1 — catalan-v2 (drafted 2026-09-29, final text 2026-10-18)

**This amendment supersedes the v1 text below wherever they conflict.** The v1
text is kept unchanged, as the record of what was declared first.

### Why amend, and why it is legitimate now

- **Nothing has been peeked at.**
  - v1 scored the forward log from 2026-08-07, but price data first overlapped
    it on 2026-09-27, when the Turso backfill landed.
  - From that day the NO-PEEK rule has been in force: no verdict has been
    joined to any return, in any form.
  - The data audit (`catalan/audit/`) read no prices.
  - When this text was committed, `price_history` ended on 2026-09-25, so no
    counted-period return was even in the database.
  - This amendment is therefore set blind to every gate statistic, as v1's
    thresholds were.
- **The data audit found the v1 instrument unfit to count**
  (`catalan/audit/output/data_audit_report.md`, 26,585 records):
  1. The v1 dedup rule (0.6 similarity on the whole headline) dropped 13.3% of
     the corpus. 63% of the drops differed in category from their keeper, and
     18 of 22 hand-read drops were distinct events.
  2. No temperature was sent, so verdicts were sampled at 1.0. Of 1,334
     headlines re-scored about 15 times, 42% changed verdict.
  3. 52% of headlines are label-only: the text, minus the company name, is
     shared by ≥ 5 companies. The substance of results and ratings is in the
     PDF.
- **Owner decisions:**
  - Long-only (2026-09-27): the model surfaces buy candidates; the owner
    decides what to buy.
  - The model `gemini-3.5-flash-lite` (2026-10-18), chosen over Claude Haiku
    4.5 to score at no cost.
- **Timeline.**
  - Drafted 2026-09-29 for a 2026-10-01 start.
  - The work was interrupted until 2026-10-18. Independent verification then
    changed the design, including the model and the universe.
  - The clock start was moved to **2026-10-26**, so this text precedes every
    counted announcement. Announcements dated 2026-08-07 → 2026-10-25 are the
    **pilot**.
- **This is a new study version.** Per this project's own rule (gate G2: a
  change to a frozen parameter after scoring has begun is a new study version),
  this is **catalan-v2**.

### What changes

| | v1 | **v2** |
|---|---|---|
| Primary portfolio | long-short, YES vs NO | **long-only**: YES-basket drift in excess of the universe |
| Model / sampling | Claude Haiku 4.5, no temperature sent (1.0) | **`gemini-3.5-flash-lite`, temperature 0**. Every response must report `modelVersion` equal to the requested model, or scoring halts. |
| Instrument | paper prompt over the headline | **PDF arm**: paper prompt over headline + first 3 PDF pages (hash `55503e79581ae655`). Falls back to the headline arm (hash `1cc1a88738863a44`) when there is no clean text. |
| Universe | point-in-time NIFTY500 roster (Turso) | **NSE's NIFTY500 list as published 2026-10-18**, frozen (`data/universe/nifty500_2026-10-18.csv`, sha256 pinned): EQ series only, minus TRENT, CUB, HEGAM and DUMMYHEG. **491 symbols.** The Turso roster could not be used: it lacks the September 2026 reconstitution. |
| Scoring scope | every announcement | **study-universe announcements only** |
| Overnight | by clock time only | by clock time on a session date; **a filing on any non-session date is overnight** |
| Sessions | weekday or weekend dates passing the breadth floor | **weekday** dates passing the breadth floor. Weekend-dated bars (Muhurat, budget Saturday) are never sessions. |
| Dedup | whole headline, DL similarity > 0.6 | same symbol, IST day and category; **identical** filing text (no filer name, no template stem; `DEDUP_SIMILARITY` = 1.0); different NSE attachment sizes are never the same filing |
| Category / headline exclusions | not frozen | **frozen**: Trading Window, Copy of Newspaper Publication, Disclosure under SEBI Takeover Regulations; headline text "copy of newspaper publication" |
| Counted data | forward log from 2026-08-07 | announcements dated **≥ 2026-10-26** (`STUDY_CLOCK_START`), attributed to a session **≥ 2026-10-27** |

### Definitions

- **Session.** A weekday date that is present in `price_history` and passes the
  panel's session rules (breadth floor). Weekend-dated bars are not sessions.
- **Overnight announcement.** One published after 15:30 IST, or before 09:00
  IST, on a session date, **or at any time on a non-session date** (weekend or
  exchange holiday). Its session is the next session (`panel.attribution_date`).
- **Usable counted session.** A session t ≥ 2026-10-27 on which at least 80%
  (`USABLE_SESSION_MIN_COVERAGE`) of the study universe has a valid panel row
  (`drift_ret`). Prices count whenever they are loaded; the panel is rebuilt
  from `price_history` at evaluation.
- **Valid stock-session.** A study-universe stock on session t that has a
  valid panel row and at least 20 prior sessions of `price_history`, so that
  its ADV and spread are defined. The spread additionally needs at least 15
  usable pairs (see the cost definition). A stock-session that is not valid is left out
  of the basket, the universe return and the buckets that day.
- **Eligible announcement.** All of the following hold:
  - dated ≥ 2026-10-26;
  - the symbol is in the study universe;
  - it is overnight, and attributed to a usable counted session;
  - it is not excluded by the frozen exclusions;
  - it survives the v2 dedup rule.

  Announcements attributed to a session before 2026-10-27 are dropped. For
  example, a filing before 09:00 on 2026-10-26 belongs to session 2026-10-26,
  which is not counted.
- **Verdict.** Only score rows with `model_id` = `gemini-3.5-flash-lite`,
  temperature 0.0, and the arm's own prompt hash count.
  - The PDF arm's *parsed* verdict, when one exists.
  - Otherwise the headline arm's parsed verdict, flagged as a fallback.
  - A response with no readable verdict (an abstention) counts as no verdict
    from that arm.
  - An announcement with no parsed verdict from either arm is **dropped**, not
    scored 0.
  - The fallback share and the dropped share are both reported.
- **Stock-session score.** The mean verdict (+1 / 0 / −1) of the stock's
  eligible announcements attributed to session t.
  - **YES basket:** score > 0.
  - **NO basket:** score < 0 (used by C0 only).
  - Equal weights.
- **Universe return on t.** The equal-weighted `drift_ret` of every valid
  stock-session on t.
- **Excess series.** e_t = mean `drift_ret`(YES basket) − universe return, at
  100% daily rebalancing.
  - A basket stock whose stock-session is not valid is dropped for that day.
  - **An empty YES basket gives e_t = 0 and c_t = 0** (no position, so no cost
    and no hedge). The session still counts toward the 120.
- **Liquidity bucket.** The within-date quintile of 20-session rupee ADV
  (`adv_20d`) across that day's valid study-universe stock-sessions (1 = least
  liquid). It is **recomputed at evaluation over `study_universe()`**. The
  panel's stored `in_universe` and `liquidity_bucket` columns come from the
  stale Turso roster and are **not** used. For bucket-level series, the
  benchmark is the bucket's own equal-weighted universe.
- **Cost c_t** (C2). The mean, over YES-basket stocks, of a round-trip cost on a
  position of **₹1,00,000** (`POSITION_NOTIONAL_INR`):
  - **Explicit costs:** the cost table below, at that notional.
  - **Spread:** the stock's **Abdi–Ranaldo (2017)** window estimator over the
    previous 20 sessions (`SPREAD_ESTIMATOR`, `SPREAD_WINDOW_SESSIONS`), on log
    prices:
    - s = √max(0, 4 · mean[(c_τ − η_τ)(c_τ − η_{τ+1})]);
    - c is the log close, and η the mean of the log high and log low;
    - the mean is taken over the pairs (τ, τ+1) of consecutive grid sessions in
      the window where the stock has a bar on both;
    - with fewer than 15 such pairs (halts, gaps), the stock-session is not
      valid that day.

    It is charged in full per round trip.
  - **Hedge:** plus **1 bp** (`HEDGE_COST_BPS`) for neutralising market drift
    with index futures.

  No other impact term is charged; at ₹1,00,000 per position, participation in
  any study-universe stock is negligible.

### Gates

**C0: pipeline check.**
- h_t = 1[mean `init_ret`(YES basket) − mean `init_ret`(NO basket) > 0], with
  `init_ret` = close(t−1) → open(t).
- A tie is a miss.
- Stocks whose `init_ret` is NULL (the panel's corporate-action guard) are left
  out of both C0 baskets that day.
- Sessions where either basket is empty are not C0 observations.
- **Threshold: h_t = 1 on ≥ 70% of the first 40 usable counted sessions that
  have both baskets non-empty.** Evaluated once.
- No short trade is involved, because the initial reaction is not tradable.
- **A fail stops the study:** C1 and C2 are not evaluated.

**C1: long-only excess drift, gross.** Mean e_t > 0 with t = mean/(sd/√N) ≥
2.0 (sample sd, ddof = 1), over the first 120 usable counted sessions. Necessary, never sufficient;
never reported without C2 beside it.

**C2: long-only excess drift, NET of costs. THE SHIP GATE.**
- n_t = e_t − c_t, the **pooled** daily series. All four conditions must hold
  on it:
  1. t ≥ 2.0
  2. mean ≥ 5 bps/day
  3. annualized Sharpe (daily Sharpe × √252) ≥ 0.5
  4. **Deflated Sharpe** (Bailey & López de Prado, 2014) **> 0.95**, computed
     with:
     - K = 5 trials (the five liquidity-bucket cells)
     - cross-trial Sharpe variance = the sample variance (ddof = 1) of the
       five bucket-level net daily Sharpes
     - skewness and **non-excess** kurtosis of the pooled n_t, as in the
       Bailey–López de Prado formula
     - T = the number of usable counted sessions
     - all Sharpes daily and not annualized inside the formula
- **Bucket-1 rule.** If C2 passes on the pooled series, recompute it with
  bucket-1 stocks removed from **both** the basket and the benchmark: the
  buckets 2–5 basket against the buckets 2–5 equal-weighted universe. If that
  series has t < 2.0, the result is recorded as **negative**: the edge lives
  only where trading costs are least trustworthy.
- Bucket-level results are reported for all five buckets; they gate nothing.
- *Declared limitation:* the benchmark is the equal-weighted study universe,
  while a practical hedge (NIFTY index futures) is cap-weighted. The basis
  between them is not modelled.

**Primary endpoint:** the pooled series from the PDF arm with headline
fallback, at 100% rebalancing. The deflation trial count is K = 5.

**Secondary endpoints.** Reported, gate nothing, never promotable:
- the headline arm alone
- long-short drift (needs the T2T/ASM/GSM short filter, open item 4)
- 25% rebalancing
- the unhedged raw return (basket drift − c_t + hedge allowance)
- the subset without fallbacks
- close-to-close horizons of **1, 2 and 5 days**, with **delivery** costs (STT
  0.1% each side plus stamp duty: about 22–25 bps round trip plus spread)

v1's other model arms and non-LLM baselines are **not run** in v2.

### Horizon, stopping rule and power

- **Horizon:** C1 and C2 are evaluated **once, at 120 usable counted
  sessions**. No extension, no second look. "Inconclusive" (|t| < 2) is a
  declared outcome.
- **Power, declared blind:**
  - *Assumption:* e_t has a daily standard deviation of about **129 bps**. That
    is the paper's long-short volatility (182 bps) divided by √2.
  - At N = 120, t ≥ 2 needs a daily Sharpe of 0.183, i.e. **≈ 24 bps/day
    gross excess**.
  - The assumption is deliberately conservative. An equal-weighted basket of
    tens of names against an equal-weighted universe is likely less volatile,
    so the true detectable effect is likely smaller.
  - The paper's US long leg earned ≈ 8 bps/day, so a US-sized long-only effect
    may still read inconclusive.
  - *Declared interpretation:* the declared round trip costs ≈ 8 bps of
    explicit cost at ₹1,00,000, plus the spread, plus 1 bp hedge: roughly 15–30
    bps in all. A long-only edge too small to detect here is also too small to
    trade at this study's scale. "Inconclusive" is read as **"not worth trading
    long-only"**, never as "no information".

### The model and the free tier

- **Model check (2026-10-18).** `gemini-3.5-flash-lite`:
  - Google lists no shutdown date for it.
  - It reported `modelVersion` equal to the requested name.
  - It used no thinking tokens and accepted temperature 0.
  - It gave identical verdicts on three repeats of three test headlines.
- **Model drift.** A response counts only if both hold:
  - it reports `modelVersion` exactly equal to `gemini-3.5-flash-lite` (any
    other string, including a suffixed one such as `…-001`, is a different
    model);
  - it used **no thinking tokens** (a declared property of the instrument).

  Otherwise scoring halts for that run: nothing more is written, and the run
  turns red. Every stored verdict therefore comes from a matching response.
  - Rows written before a halt remain valid.
  - If a later run gets matching responses again, scoring resumes
    automatically. That needs no decision, because every row is checked
    individually.
  - Every run appends its model check, one row per arm, to
    `forward_log/model_checks/`. Statuses:
    - `ok`: matching responses
    - `drift`: another model, or thinking tokens
    - `model_gone`: HTTP 404 for the model name
    - `error`: any other stage failure
    - `no_match`: calls attempted, none stored
    - `quota`: daily cap spent
    - `idle`: nothing to score
  - **"Instrument withdrawn"** means one of two things. Either 10 consecutive
    *counting days*, or Google's announced shutdown of the model.
    - A counting day is a calendar day whose checks include `drift` or
      `model_gone` and no `ok`.
    - A day with any `ok` resets the count.
    - Days with no check, or only `idle`, `quota`, `error` or `no_match`, are
      operational: an expired key, a Google outage, a bug. They neither count
      nor reset. An operational failure must never be able to end the study.
    If either happens before 120 usable counted sessions have verdicts, the
    study ends **"inconclusive: instrument withdrawn"**. Any continuation on
    another model is catalan-v3, with its own clock.
  - *Known limitation:* a silent change that keeps the same name, uses no
    thinking tokens and still answers readably cannot be detected. The workflow
    turns red when more than 20% of a run's answers have no readable verdict,
    which is how such a change would most likely first show.
- **Free tier.** About 500 requests/day. The pipeline needs ≈ 325 on a typical
  day and up to ≈ 625 on peak results days.
  - The PDF arm (the primary endpoint) is scored before the headline arm, so a
    spent quota defers the control first. A spent quota defers the rest to the
    next run. Scoring later cannot leak
    outcomes: the model has no tools, no browsing, and a training cutoff before
    the counted period.
  - Moving to paid billing on the same model is the same instrument.
  - Google may use free-tier inputs for training. The inputs are public NSE
    filings.

### The pilot, the owner's picks, and peeking

- **Pilot.** Announcements dated 2026-08-07 → 2026-10-25.
  - May be analysed only **after this amendment is committed**, and only to
    validate the pipeline: entity linkage, the session join, the returns join.
  - **Nothing learned from pilot returns may change a v2 parameter.** Doing so
    requires catalan-v3.
- **The owner's own buy decisions,** taken from the daily buy list
  (`forward_log/buy_list/`):
  - logged separately, starting after C0 passes;
  - never part of any gate;
  - reported as a distinct track.
- **NO-PEEK.** Outside the pilot validation, no statistic joining v2 verdicts to
  returns is computed before C0 (at 40 sessions) or C1/C2 (at 120). That
  includes the buy-list files, which carry no prices by design.

### Frozen parameters added or changed by v2

Pinned by gate G2 (`tests/test_frozen_params.py`), together with golden tests of
the dedup text rule and of the PDF text extraction.

| Parameter | v2 value |
|---|---|
| `STUDY_VERSION` | `catalan-v2` |
| `STUDY_CLOCK_START` | `2026-10-26` |
| `PRIMARY_MODEL` | `gemini-3.5-flash-lite` |
| `TEMPERATURE` / `TEMPERATURE_MODEL_PREFIXES` | 0.0 / `claude-haiku-4-5`, `gemini-3.5-flash-lite`; stored per score row |
| `UNIVERSE_FILE` / `UNIVERSE_FILE_SHA256` | `data/universe/nifty500_2026-10-18.csv` / `2959bf20…5cb3` |
| `UNIVERSE_SERIES` / `UNIVERSE_EXCLUDED` | `EQ` / DUMMYHEG, TRENT, CUB, HEGAM |
| `DEDUP_SIMILARITY` | 1.0 (identical filing text), same category, attachment-size rule |
| `PDF_PAGE_WINDOW` | 3 |
| `PDF_MIN_PAGE_CHARS` | 50 |
| `PDF_GARBLE_MAX` / `PDF_GARBLE_ALLOWED` | 0.15 / Devanagari, ₹ and typographic punctuation |
| `PDF_MAX_CHARS` | 30,000 (truncation flagged) |
| PDF readability | any unreadable page in the window → no PDF-arm verdict → headline fallback |
| ZIP attachments | the first `.pdf` member by name |
| `HEDGE_COST_BPS` | 1.0 |
| `POSITION_NOTIONAL_INR` | 100,000 |
| `SPREAD_ESTIMATOR` / `SPREAD_WINDOW_SESSIONS` | Abdi–Ranaldo (2017) / 20 |
| Category / headline exclusions | frozen (`CATEGORY_EXCLUSIONS_FROZEN = True`) |
| Prompts | headline arm `1cc1a88738863a44`; PDF arm `55503e79581ae655` |

---

## What is being tested

Do Claude's readings of NSE corporate-announcement headlines predict the
announcing stock's **same-session drift** (`open(t) → close(t)`), and does that
prediction survive Indian round-trip trading costs?

Replicates Lopez-Lira & Tang, JFE 184 (2026) 104335. US benchmarks: 93.3% daily
hit rate on the initial reaction, 34 bps/day gross on drift, 2.97 annualized
Sharpe, **unprofitable at 20 bps round trip**.

## The two arms, and which one counts

| Arm | Corpus | Status |
|---|---|---|
| **Forward log** | announcements collected after the study started | **The only ship-gate evidence.** |
| Full history | 2019 → now | Exploratory. Used to *measure* contamination, never to claim an edge. |

Claude Opus 5's training cutoff is May 2026 and today is past it. A 2019→2026
backtest scored by Opus 5 is a model recalling what happened, not predicting it.
The forward arm is immune by construction, which is why it alone clears C2.

---

## Gates

### C0 — Comprehension. **Not a trading claim.**

Long-short **initial-reaction** (`close(t-1) → open(t)`) portfolio hit rate on
the forward log, over overnight announcements in the PIT NIFTY500.

This leg is **not tradable** — you cannot trade before the news prints. C0 is a
pipeline smoke test: it verifies that entity linkage, session classification and
the returns join all work. Failing C0 means something upstream is broken, **not**
that the market is efficient.

- Paper's US figure: 93.3%
- **Threshold: daily long-short hit rate ≥ 70%**
- **Minimum observations: 40 trading days**

40 days at 70% is 28/40, one-sided binomial *p* = 0.0083 against a fair coin —
comfortably inside a 1% bar. Deliberately far below the paper's 93.3%: C0 asks
"is the pipeline wired correctly", not "does India match the US". A pass means
entity linkage, session classification and the returns join work. **A fail means
something upstream is broken and C1/C2 must not be evaluated at all** — it is
not evidence about market efficiency.

C0 is evaluable long before C1/C2 and should be run as soon as 40 sessions of
overlapping price data exist, precisely so a broken pipeline is caught early
rather than after a year of collection.

Supplementary diagnostic, reported alongside but carrying no gate authority:
the **per-announcement** initial-reaction hit rate. At ~107 directional
in-universe overnight names/day it reaches tight bounds far faster than the
daily portfolio statistic, which makes it the better early warning — but the
portfolio form is what the paper reports, so the portfolio form is the gate.

### C1 — Drift, gross of costs

Daily long-short **drift** (`open(t) → close(t)`) return on the forward log.

- Reported: mean daily return (bps), t-statistic with **date-clustered** standard errors, hit rate, annualized Sharpe
- **Threshold: mean daily long-short drift > 0 with t ≥ 2.0** (date-clustered SE)
- **Minimum observations: 120 trading days** — see *Horizon and stopping rule*

C1 is a **necessary, not sufficient** condition. It gates nothing on its own and
is never reported without C2 beside it.

### C2 — Drift, NET of costs. **THIS IS THE SHIP GATE.**

The same statistic after the cost model below, reported **per liquidity bucket**.

**Threshold — all four must hold simultaneously:**

| # | Condition | Why it is here |
|---|---|---|
| 1 | mean daily net LS drift **> 0 with t ≥ 2.0**, date-clustered SE | statistical significance |
| 2 | mean daily net LS drift **≥ 5 bps** | economic materiality |
| 3 | **net annualized Sharpe ≥ 0.5** | materiality, scale-free |
| 4 | **Deflated Sharpe > 0.95** over the declared cells | multiple testing |

- **Minimum observations: 120 trading days.**
- Conditions 2 and 3 exist because with enough days, t ≥ 2 can certify an edge
  far too small to trade. R2 is the cautionary case in the other direction — it
  passed gross and died on costs — and a bar that certifies 1 bp/day would be
  the same mistake wearing different clothes. At N = 120 these floors are
  **insurance, not the active bar**: they correspond to daily Sharpe 0.028 and
  0.032 against the 0.183 that condition 1 demands.
- **C1 without C2 is not a result.** No gross number is reported anywhere —
  API, UI, or write-up — without its net companion beside it.
- If the edge survives **only** in the most illiquid bucket, that is recorded as
  a **negative result**. Stated here, before any number is seen, so it cannot be
  re-argued later.

### Multiple-testing control

Deflated Sharpe over the number of declared cells. **The cell count is fixed in
advance**, here, and is not the number of things that turn out to have been
tried.

**Primary endpoint — the ship gate.** One configuration, declared now:

> the **primary model arm** (`config.PRIMARY_MODEL`, Haiku 4.5), at **100%
> rebalancing**, reported across the **5 liquidity buckets**.

**Declared cell count for the deflation: K = 5.**

**Secondary endpoints — reported, but they gate nothing.** The other two model
arms, the three non-LLM baselines, and the 25% rebalancing variant. 55 further
configurations, all reported for completeness and none of them able to clear C2.
A secondary cell that looks better than the primary is **not** promotable: that
is the multiple-testing failure this section exists to prevent, and swapping the
primary after seeing results would void the pre-registration.

**Why 5 and not 60.** An earlier draft crossed model arms with baseline arms
(3 × 3 × 5 × 2 = 90), which is arithmetically wrong — a headline is scored by
*one* scorer, so the six scorers are alternatives, not a product. The honest
full-grid count is 6 × 5 × 2 = 60. Deflating over all 60 while only one
configuration can actually clear the gate is a self-inflicted penalty: it raises
the required effect from 58% of the paper's to 158% of it, turning a ~2-month
study into a 7.5-month one at best and a 2.5-year one if the Indian effect is
half the US figure. Primary/secondary endpoint separation is the standard
answer and is what is declared here.

### Horizon and stopping rule

**C1 and C2 are evaluated exactly once, at 120 trading days of forward log with
usable price data. There is no second look and no extension.**

This is the rule the rest of the document depends on. Without a fixed horizon
the natural behaviour — keep collecting, re-check, stop when the t-statistic
crosses 2 — is optional stopping, and it inflates the false-positive rate
without leaving a trace in the reported numbers. A pre-registration that omits
it is decoration.

Three consequences, all accepted in advance:

1. **"Inconclusive" is a declared outcome, not a failure to be worked around.**
   If |t| < 2.0 at 120 days the study reports *underpowered — no result* and
   stops. It does not keep collecting until significance appears.
2. **A near-miss is a miss.** t = 1.9 at day 120 is inconclusive. Extending to
   day 140 to see whether it crosses is exactly the prohibited move.
3. **The 120-day clock starts when price data and the forward log overlap**, not
   when collection started. As of 2026-08-20 the overlap is **zero days** — see
   the blocker note below.

**Declared statistical power, so "inconclusive" is interpretable.**

Taking the paper's own ratio (34 bps/day at 2.97 annualized ⇒ daily Sharpe
0.187, implying ~182 bps/day long-short volatility):

| Constraint at N = 120 | Minimum detectable daily Sharpe | As % of the paper's effect |
|---|---|---|
| t ≥ 2.0 | 0.183 | **98%** |
| Deflated Sharpe, K = 5 | 0.109 | 58% |

**t ≥ 2.0 is the binding constraint, and 120 days can only detect an effect
essentially as strong as the US result** — about 33 bps/day. An Indian effect at
70% of the paper's would need ~233 days and will read as inconclusive here; at
half, ~457 days. This horizon was chosen with that limitation understood. It
buys a fast, clean answer to *"is there a US-sized effect in India?"* and
explicitly does not answer *"is there a smaller one?"*

A future study may ask the smaller question. It must do so under a **new
pre-registration with its own horizon** — not by extending this one.

### Blocker on the clock

> **Resolved 2026-09-27 (Amendment 1).** Turso `price_history` now runs to
> 2026-09-25, and 497 of 500 PIT NIFTY500 members have prices on every date
> checked from 2026-07-31. The v2 clock is defined by `STUDY_CLOCK_START`, not by
> this overlap. Prices after 2026-09-25 are added with
> `scripts/price_gap_backfill/` before C0 (40 sessions) and before C1/C2 (120).
> The v1 text follows for the record.

`price_history` ends **2026-08-04**; the forward log begins **2026-08-07**. They
do not overlap, so **zero of the 120 days have been accrued** despite 8 days of
headlines being collected and scored. The clock cannot start until the price
side is repaired (Angel One → Turso). Recorded here because a horizon that
silently never starts is the same failure as no horizon at all.

### Contamination test (paper Online Appendix C)

Holding the category (`desc`) mix fixed, does a model score **better on
pre-cutoff than post-cutoff** headlines?

Holding the mix fixed matters: NSE announcement composition drifts over time, so
an unconditional pre/post comparison confounds memorisation with topic drift.

A pre-cutoff advantage quantifies memorisation and **caps what the historical
arm may claim**. Training cutoffs must be read from Anthropic's documentation
per model before the arms are assigned — assumed cutoffs invalidate this test.

---

## Cost model — declared now, not fitted later

Zerodha MIS intraday round trip, per leg-pair:

| Component | Rate |
|---|---|
| Brokerage | ₹20/order or 0.03%, whichever lower × 2 orders |
| STT | 0.025% on the sell side (intraday) |
| Exchange transaction | ~0.00297% × 2 |
| Stamp duty | 0.003% buy side |
| SEBI turnover | ₹10 per crore |
| GST | 18% on (brokerage + exchange txn) |

Explicit costs land around **4–6 bps round trip** at meaningful order size.

**The explicit part is not the risk — spread and impact are.** The paper's
strategy dies at 20 bps and its turnover was ~190%/day. Two India-specific facts
make our exit worse than the paper's:

1. **NSE's close is a VWAP of 15:00–15:30, not a call auction.** The paper
   explicitly justified flat-bps costs because both its legs cleared in auctions
   with no bid-ask spread. That justification does not hold on our exit — the
   exit pays real spread.
2. The short leg is executable intraday (MIS), which matters because the paper
   finds the short leg carries most of the alpha (26 bps vs 8 bps daily). **But**
   NSE bans intraday shorting in trade-to-trade (T2T) and surveillance (ASM/GSM)
   names. Those are flagged per-day and excluded from the short leg. A short-leg
   result computed without that filter is not reportable.

Therefore:

- Spread/impact is estimated **per liquidity bucket** from `price_history` and
  traded volume. One flat number across the cross-section is not acceptable.
- Results are reported per bucket, because the paper's alpha concentrates in
  small caps — exactly where impact is worst.
- The paper's §6.2 partial-rebalancing variant is included (25% rebalancing cut
  turnover 190%→46%/day and *improved* net Sharpe at 10 bps).

---

## Frozen study parameters

Set in `catalan/config.py`; changing any of them after scoring begins
invalidates the run.

| Parameter | Value |
|---|---|
| Universe | point-in-time NIFTY500. **v2:** NSE's NIFTY500 list frozen on 2026-10-18, EQ series only, minus TRENT/CUB/HEGAM/DUMMYHEG (491 symbols); only these are scored (Amendment 1). |
| Overnight window | after 15:30 IST on t−1, or before 09:00 IST on t. **v2:** also any time on a non-session date (weekend, exchange holiday); weekend-dated bars are never sessions. |
| Initial reaction | `close(t−1) → open(t)` — not tradable |
| Drift | `open(t) → close(t)` — the tradable leg |
| Dedup | ~~same symbol, same day, Damerau–Levenshtein similarity > 0.6 → keep earliest~~ **v2:** same symbol, IST day and category; identical filing text; different attachment sizes are different filings → keep earliest |
| Category exclusions | **v2: FROZEN** — Trading Window, Copy of Newspaper Publication, Disclosure under SEBI Takeover Regulations. (v1: not yet frozen.) |
| Headline-text exclusions | **v2: FROZEN** — `config.HEADLINE_EXCLUSIONS` ("copy of newspaper publication"). (v1: not yet frozen.) Matched on the text, so a row reading "… Copy of Newspaper Publication" is dropped whatever `desc` NSE assigned it. Caught 0 extra rows on 2026-08-07; a guard against vendor-category drift, not a present leak. |
| Score mapping | YES → +1, UNKNOWN → 0, NO → −1 |
| Prompt | the paper's, verbatim — **transcribed 2026-08-10** from Section 5, p.5. Hash `1cc1a88738863a44`. **v2:** this is the headline arm; the PDF arm's prompt is `55503e79581ae655` (Amendment 1). Both at temperature 0, on `gemini-3.5-flash-lite` with a served-model check on every call. |
| Liquidity buckets | 5, within-date quintiles by 20-day ADV. **Orientation: 1 = LEAST liquid … 5 = MOST liquid.** |
| ADV measure | **rupee turnover** (`close × volume`), 20 sessions, `min_periods` = the full window |
| Corporate-action guard | `abs(init_ret) > 0.20` → null `init_ret` **and** `prev_close`; retain `open`, `close`, `drift_ret` |
| `prev_close` | the immediately preceding **global trading session's** close, or NULL. **No forward-fill.** |
| Phantom-session floor | a date is a session only if its breadth ≥ `max(20, 0.25 × median breadth)` over the window |
| Weekly-cadence guard | per symbol, rolling-10 median bar gap > 1.6 sessions → row dropped entirely |
| Flat-bar guard | `open == high == low == close` → row dropped entirely |

### Panel construction — declared consequences

These are stated here because each one decides which rows enter C0/C1/C2, and
because two of them are known, uncorrected biases.

**The corporate-action guard costs the ship gate nothing.** On an ex-date all
four of a session's prices are already quoted in the post-action basis, so only
the comparison to the *previous* close crosses the basis change. `drift_ret =
close(t)/open(t) − 1` is unaffected. The guard therefore protects only the
untradable C0 leg. It must never be "simplified" into dropping the row: that
would delete drift observations on exactly the highest-news-intensity days.

**Declared residual — ex-dividends and rights issues are not caught.** A 1–2%
dividend yield sits far inside the 20% band, so `init_ret` carries a small
negative bias on ex-dates. This is stated rather than silently corrected,
because correcting it would require a corporate-actions table this project does
not have.

**Declared consequence — `prev_close` is not forward-filled.** If a stock was
halted on t−1, `open(t)/close(t−2) − 1` is a two-day return wearing an
overnight label. Those rows get a NULL `init_ret` instead. Losing an
observation is cheap; a mislabelled horizon in the C0 leg is not.

**Declared consequence — the pre-2025-03-17 contamination is dropped, not
adjusted.** `price_history` rows from before that cutover were weekly
aggregated OHLC, and earlier RapidAPI-era rows copied one price into all four
OHLC fields (giving `drift_ret` an *exact* 0.0 — a fake zero in the tradable
leg, worse than noise because it shrinks the mean toward zero). Both are
dropped. This means the historical arm's row count is materially reduced in a
way that is **not random across time**, which is one more reason the forward
log, not the backtest, is the ship gate.

### Observed panel behaviour (2026-08-19, dry run over 2019-01-01 → 2026-08-04)

Recorded because it was measured before any gate was evaluated, and because it
revises an assumption this document was drafted under.

| Statistic | Value |
|---|---|
| Sessions / symbols / rows fetched | 1,880 / 840 / 1,283,881 |
| Rows written | 1,279,354 (99.6%) |
| Rows in PIT NIFTY500 | 921,518 (71.8% of written) |
| Dropped — flat bar | 4,227 (0.33%) |
| Dropped — phantom session | 161 (0.01%) |
| Dropped — weekly cadence | 139 (0.01%) |
| Corporate-action suppressions | 25 in 7.6 years |

**The weekly-aggregate contamination this document anticipated is essentially
absent.** 139 rows, not a large non-random fraction. The guard stays — it costs
nothing and a future re-backfill could reintroduce the problem — but the
historical arm is not disqualified on data quality. It remains **exploratory
only**, for the separate and unchanged reason that model training cutoffs fall
inside the window.

**`price_history` appears to be split-adjusted**, contrary to
`backfill_bhavcopy.py:33`. Verified against TATASTEEL 1:10 (ex 2022-07-29),
WIPRO 1:3 bonus (2019-03) and IRCTC 1:5 (ex 2021-10-29): none shows a basis
jump. This makes `init_ret` more trustworthy than assumed, and makes the
corporate-action guard near-vacuous rather than load-bearing. **The declared
ex-dividend residual above is unaffected** — dividend adjustments are a separate
question from split adjustment and remain uncorrected.

**Fewer than 5 eligible symbols on a date → that date gets NULL buckets**,
never 1..k for k < 5. Assigning 1..4 would file a mid-cap under "bucket 1 =
most illiquid", and bucket 1 is the cell this document reads its negative
verdict off.

## Observed pilot behaviour (2026-09-27 data audit, 26,585 records, no returns read)

From `catalan/audit/output/`, and measured before Amendment 1 was written:

| Statistic | Value |
|---|---|
| Records, 2026-08-07 → 2026-09-25 | 26,585; 2,370 symbols; 113 categories |
| Inside PIT NIFTY500 | 26.6% |
| Label-only headlines (text shared by ≥ 5 companies) | 51.9% |
| Strict duplicates | 3.1% |
| Dropped by the v1 dedup rule | 13.3% (63% of those differ in category from their keeper) |
| Verdict split (first real verdict) | 47.5% NO, 33.7% UNKNOWN, 18.6% YES |
| Re-scored headlines that changed verdict at temperature 1.0 | 42% (566 / 1,334) |
| Overnight share (after 15:30 or before 09:00) | 68.5% |

## Observed corpus behaviour (2026-08-10, n=225, Haiku 4.5)

Recorded here because it was measured *before* thresholds were set, and it
constrains what those thresholds can reasonably be.

| Statistic | Value |
|---|---|
| Announcements, 2026-08-07 | 1,286 across 659 symbols |
| Inside PIT NIFTY500 | 411 (32.0%) |
| After category exclusions + dedup | ~900/day |
| Verdict split | 23.4% YES, 38.0% UNKNOWN, 37.6% NO |
| Parse-miss rate | ~1% (abstain, retried next run) |

61% of headlines receive a directional verdict, so there is real cross-sectional
dispersion to form portfolios on. The 32.0% in-universe share is **below the
~44% the study plan probed for June-2026** — one day is not a month and 07-Aug
was peak results season, but if it persists the corpus projections (~500k rows
2019→2026) are overstated.

## Open items blocking finalization

> **Status under v2 (2026-10-18):**
> - Item 3 is **closed** by Amendment 1: exclusions are frozen.
> - Item 7 is **closed**: prices are backfilled.
> - Item 4 no longer blocks the primary endpoint: long-only needs no short
>   filter. It still blocks the long-short secondary.
> - Item 1 remains open. It matters only for the historical arm; the forward
>   arm post-dates every candidate model's training data.

1. Verify documented training cutoffs for Haiku 4.5 / Sonnet 5 / Opus 5
2. ~~Transcribe the paper's prompt verbatim~~ — done 2026-08-10
3. Freeze the `desc` exclusion list
4. Source T2T / ASM / GSM daily lists for the shortability flag
5. ~~Set every `TBD` threshold above~~ — done 2026-08-20
6. ~~Declare the cell count for the deflated Sharpe~~ — done 2026-08-20 (K = 5)
7. Repair the price/forward-log overlap so the 120-day clock can start
