# Returns panel construction

How CATALAN turns `price_history` into the two return legs the study measures,
and — more importantly — which rows it refuses to measure and why.

The paper (Lopez-Lira & Tang, JFE 184) works on CRSP, where the return series is
already cleaned, split-adjusted and calendar-aligned. None of that is true of
`price_history`. Every guard on this page exists because a specific property
CRSP guarantees is absent here.

## The two legs

```
init_ret  = open(t) / close(t-1) - 1    the market's immediate reaction. NOT tradable.
drift_ret = close(t) / open(t)   - 1    the tradable leg. This is the study.
```

The paper's tradable claim is **drift**, not the initial reaction: prices keep
moving in the LLM's direction for 1-2 days at ~34 bps/day gross, and the
strategy dies at 20 bps round-trip cost. So `drift_ret` is the only column the
C2 ship gate reads, and every design decision below is scored on what it does to
that column specifically.

## What CRSP guarantees and NSE-via-Turso does not

| CRSP property | Reality here | Guard |
|---|---|---|
| A trading calendar | No holiday calendar exists anywhere in the repo | Session grid inferred from breadth |
| Split/bonus adjustment | OHLC is unadjusted (`backfill_bhavcopy.py:33`) | Corporate-action guard |
| One row per symbol-session | Multiple backfills, overlapping eras | Duplicate + cadence + flat-bar guards |
| Uniform bar frequency | Pre-2025-03-17 rows are weekly aggregates | Cadence guard |
| A stable universe | NIFTY500 reconstitutes twice a year | PIT `MembershipRegistry` |

## The session grid, and why it comes first

There is no holiday calendar, so sessions are inferred: a date in
`price_history` is a session only if its breadth clears
`max(PANEL_MIN_SESSION_BREADTH_ABS, PANEL_MIN_SESSION_BREADTH_REL × median)`.

A relative floor with an absolute backstop, because a constant tuned for 2026's
~452-symbol coverage would reject most of 2019, when coverage was genuinely
thinner.

This has to happen before anything else, because a phantom session is not a
cosmetic problem. It shifts **every** symbol's `prev_close` back by one, and it
becomes a date `attribution_date()` can return — so an announcement would be
attributed to a day the market never traded.

**Measured consequence:** Diwali Muhurat sessions (~1-hour ceremonial sessions,
e.g. 2020-11-14 with 111 bars) fall below the floor and are excluded. Defensible
for a drift study — a 1-hour session's open→close drift is not the same
quantity — but it is an exclusion, not an accident, and it is recorded in
`PanelReport.sessions_rejected`.

**Known weakness:** the floor measures *raw* breadth, not in-universe breadth.
2026-08-04 carries 238 bars (>25% of median, so accepted) of which only **11**
are NIFTY500 members, against ~225 on a normal session. A partial write can
therefore pass the floor while carrying a badly non-random cross-section. Treat
the last session of any build as suspect until the price feed is confirmed
complete for that day.

## `prev_close` is a global-grid lag, not a per-symbol one

A per-symbol positional lag is wrong. Pivot onto the session grid, then shift once:

```python
close_w = kept.pivot(index="date", columns="symbol", values="close").reindex(grid)
prev_w  = close_w.shift(1)          # exactly one global session back
```

`reindex` introduces NaN wherever a symbol had no bar; `shift` propagates it.

**Declared rule: no forward-fill.** If a stock was halted on *t−1*, then
`open(t)/close(t−2) − 1` is a two-day return wearing an overnight label. Losing
an observation is cheap; a mislabelled horizon in the C0 leg is not.

## The corporate-action guard, and why it costs the ship gate nothing

No corporate-actions table exists in this repo and OHLC is unadjusted, so a
split reads as a fake overnight return. Rule: if `abs(init_ret) > 0.20`, null
`init_ret` **and** `prev_close`, and **retain `open`, `close` and `drift_ret`
unchanged**.

The retention half is a fact, not a compromise. On an ex-date all four of the
session's prices are already quoted in the post-action basis, so only the
comparison to the *previous* close crosses the basis change.
`drift_ret = close(t)/open(t) − 1` is entirely inside the new basis and is
untouched.

**So the guard protects only the untradable C0 leg and costs C2 nothing.** Any
future refactor that "simplifies" it into dropping the row would delete drift
observations on exactly the highest-news-intensity days — the worst possible
rows to lose in a news-reaction study.

0.20 is defensible against NSE price bands (5/10/20%, 10% dynamic for F&O): a
>20% overnight move that is not a corporate action is near-unobtainable, while a
1:2 split is −50% and 1:10 is −90%.

`PanelReport.ca_suspects` reports the nearest small-denominator price ratio
(`Fraction(prev_close/open).limit_denominator(20)`) so a reader can see how many
suspects are textbook splits. That is a **diagnostic only** — as a rule, "null
it only if the ratio is close to a nice fraction" would be a threshold fitted on
the same data the study measures.

**Declared residual:** ex-dividends and rights issues are not caught — a 1–2%
yield sits far inside the band — imparting a small negative bias to `init_ret`
on ex-dates. Stated, not silently corrected.

## Two contamination guards that DO drop the row

Both target eras of `price_history` that predate the current bhavcopy backfill.

**Weekly cadence.** Before the 2025-03-17 cutover, rows were weekly *aggregated*
OHLC (`av_weekly_backfill.py:20-22`): `open` is the week's open and `close` the
week's close, so **both** legs are weekly returns wearing a session label.
Detected per symbol on its own bars via `gaps.rolling(10, min_periods=5).median()
> 1.6`, the precedent at `quant_engine/research/short_horizon.py:76`.

**Flat bar.** RapidAPI-era rows copied one price into all four OHLC fields
(`av_weekly_backfill.py:9-12`), giving `drift_ret` an **exact 0.0**. That is
worse than noise: noise widens the error bar, a fake zero shrinks the estimated
mean toward zero. Caught with `o == h == l == c`.

Unlike the CA guard, neither leaves anything salvageable, so the row goes.

**Do not confuse a flat bar with a genuine `open == close`.** Measured over
July 2026: 0.2–1.3% of bars per day close exactly at their open, scattered
across dates with no clustering, and those bars have real high/low range and
real volume. Those are real market outcomes on tick-constrained stocks, and
`drift_ret == 0.0` for them is a true observation.

## ADV, and why `min_periods` is the full window

`adv_20d = (close × volume).rolling(20, min_periods=20).mean()`, rolled
positionally on each symbol's own bars **after** the cadence mask.

- Rupee turnover, not share count: `costs.spread_impact_bps(bucket,
  participation)` needs participation = notional ÷ ADV, and 10,000 shares of a
  ₹50 stock is not comparable liquidity to 10,000 shares of a ₹5,000 stock.
- After the cadence mask, or the 20 bars would be 20 *weeks*.
- Positional, not on the grid: rolling on the grid would null the ADV of any
  stock halted once in 20 sessions.
- `min_periods` is the full 20 deliberately. A 6-bar and a 20-bar ADV are not
  comparable within one cross-section, and partials would mis-bucket freshly
  listed names — the very population the paper's alpha concentrates in.

## Quintiles

`rank(method="first")` + `ceil`, **not `pd.qcut`**: qcut raises on duplicate bin
edges, which real ADV ties among illiquid names produce, and `duplicates="drop"`
would silently return fewer than 5 buckets. Rows are sorted by symbol before
ranking so ties break deterministically rather than by Turso's response order.

**Orientation is a declared study parameter: 1 = least liquid … 5 = most
liquid.** `PREREGISTRATION.md` reads its negative-result verdict off "the most
illiquid bucket", so which integer that is cannot be an implementation detail.

**Fewer than 5 eligible names on a date → that date gets NULL buckets**, never
1..k for k<5. Assigning 1..4 would file a mid-cap under "bucket 1 = most
illiquid", and bucket 1 is the cell that decides a negative result.

## Two persistence traps

**`np.int64` binds to sqlite3 as an 8-byte BLOB.** Nothing errors;
`WHERE in_universe = 1` simply returns zero rows, forever. Every INTEGER column
goes through `int(...)` and every REAL through `float(...)`.
`test_panel.py` pins `SELECT DISTINCT typeof(in_universe)` to `'integer'` for
this reason. Never use pandas nullable dtypes (`Int64`, `boolean`) in the write
path — `pd.NA` raises `InterfaceError`.

**`INSERT OR REPLACE` is a DELETE+INSERT in SQLite.** It would reset `shortable`
to NULL on every rebuild, silently disarming the short-leg filter — and the
paper finds most of its alpha on the short leg. The panel uses
`ON CONFLICT DO UPDATE` with an explicit column list, and `shortable` appears in
neither the insert nor the update list, because a separate job will own it once
the T2T/ASM/GSM source is settled.

## Row conservation

```
rows_written + rows_warmup + sum(dropped.values()) == rows_fetched
```

Silent row loss is this study's stated main failure mode, and the durable fix is
an arithmetic invariant that breaks loudly the first time someone adds a filter
without adding a counter. It **raises** rather than asserting, because `assert`
is stripped under `python -O`.

Warm-up rows are a third bucket, deliberately not counted as drops: counting
them as drops would fail the check on every single run, and a check that always
fails gets deleted.

## Measured: the guards mostly do not fire

Dry run over 2019-01-01 → 2026-08-04 (1,880 sessions, 840 symbols, 1,283,881
rows, 61s):

| Drop reason | rows | share |
|---|---|---|
| flat_bar | 4,227 | 0.33% |
| phantom_session | 161 | 0.01% |
| weekly_cadence | 139 | 0.01% |
| duplicate_bar / no_usable_price | 0 | — |
| **written** | **1,279,354** | **99.6%** |

**The weekly-aggregate contamination these guards were written for is
essentially gone.** The cadence guard was expected to remove a large,
non-random slice of pre-2025 history; it removes 139 rows. The 2026-08-03
bhavcopy re-backfill evidently replaced that era. Keep the guard anyway — it is
cheap, and it is the only thing that would catch a regression if the price feed
is ever repaired from a weekly source again — but do not describe the historical
arm as data-quality-limited. It is not. It is limited by training cutoffs, which
is a different argument.

### `price_history` is split-adjusted, contrary to the source comment

`backfill_bhavcopy.py:33` says OHLC is unadjusted. Checked against three known
corporate actions:

| Symbol | Action | Bars around the ex-date |
|---|---|---|
| TATASTEEL | 1:10 split, ex 2022-07-29 | 95.94 → 98.10 → 103.00 — no jump |
| WIPRO | 1:3 bonus, 2019-03 | 140.89 → 140.25 → 137.20 — no jump |
| IRCTC | 1:5 split, ex 2021-10-29 | 826.03 → 817.00 → 822.15 — no jump |

Consistent with the corporate-action guard firing only **25 times in 7.6 years
across 840 symbols** — far below NSE's real split/bonus rate.

Two consequences. `init_ret` is more trustworthy than this page originally
assumed. And the CA guard is near-vacuous rather than load-bearing — which is
not a reason to remove it: it costs nothing, it touches only the untradable leg,
and a future re-backfill from an unadjusted source would silently reintroduce
the problem the guard exists to catch.

The declared **ex-dividend** residual is unaffected. Split adjustment and
dividend adjustment are separate; the latter is still uncorrected.

## Project Usage

Implemented at `catalan/data/panel.py` — `build_panel`, `load_panel`,
`trading_sessions`, `attribution_dates`. Parameters frozen in `catalan/config.py`
(`PANEL_*`, `ADV_*`, `LIQUIDITY_*`) and declared in `PREREGISTRATION.md`. Tested
in `catalan/tests/test_panel.py` (31 offline tests) and pinned by the G2
frozen-parameter gate in `catalan/tests/test_frozen_params.py`.

Consumers must read the table through `load_panel`, never their own SQL, so that
"which rows are eligible" is decided in exactly one place. `Phase 4`
(`analysis/portfolio.py`, `analysis/costs.py`) is the intended caller.

**Current blocker:** `price_history` ends 2026-08-04 while the forward log
starts 2026-08-07, so the panel cannot yet cover a single scored headline. The
price side is not advancing because Turso writes are quota-blocked. Repairing
that — by backfilling Turso from Angel One, which is how those bars got there
originally — is the prerequisite for C2, not a read-time bypass: a panel built
from live API calls could not be rebuilt identically later, which is fatal for a
pre-registered study.
