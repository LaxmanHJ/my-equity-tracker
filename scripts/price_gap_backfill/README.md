# price_gap_backfill

Keeps Turso `price_history` (daily OHLCV, NSE) current. The source is Angel One
daily candles, the same feed the existing rows came from. It has two modes:

- **Extend** (default): each stored symbol's series is extended from its last stored bar up to the last completed NSE session.
- **Seed** (`--seed`): universe symbols with **no** stored rows get a short history, from `--seed-from` to the same target.

The tool is built to be safe to re-run:

- It only issues `INSERT OR IGNORE` into `price_history`.
- It **never** writes a symbol it can't prove joins seamlessly onto what's already stored (extend), or that it can't match against NSE's official prices (seed).
- It's a **dry run unless you pass `--apply`**.

## The command

Run from the repo root:

```bash
python3 scripts/price_gap_backfill/backfill.py            # 1. dry run: fetch, check, report. Writes nothing.
python3 scripts/price_gap_backfill/backfill.py --apply    # 2. same run, then write

python3 scripts/price_gap_backfill/backfill.py --seed           # new universe members: dry run
python3 scripts/price_gap_backfill/backfill.py --seed --apply   # then write
```

Run extend first, then seed. They don't overlap: a symbol with any stored row is
never a seed, and a seed is extended by the normal mode from then on.

Read the dry-run report before step 2. In particular, read the `SKIPPED` list and
the calendar notes. A normal run takes about 5 minutes (one Angel request per
symbol at 2.5 req/s). It writes about 730 rows per missing session: a 40-session
gap is about 29k rows and a 120-session gap about 87k. Turso's free tier allows
10M row writes a month.

Options:

| Flag | Use |
|---|---|
| `--to YYYY-MM-DD` | Stop at this session instead of the default. The default is the last completed session **before today (IST)**. |
| `--symbols A,B,C` | Restrict to these stored symbols (testing, re-running a skip). |
| `--allow-today` | Permit `--to` = today. Not recommended: same-evening Angel candles have carried provisional closes. |
| `--allow-missing-sessions` | Write a symbol even if some sessions are absent, e.g. a trading suspension. The gap stays a gap. |
| `--no-bhavcopy` | Skip the NSE official-price check, e.g. if NSE blocks your IP. Refused with `--seed`. |
| `--allow-no-ca-feed` | Write even if the NSE corporate-actions feed could not be read. Without it, `--apply` **stops** when the feed is down (verifier rule C1): an unadjusted split inside the window would otherwise go undetected. |
| `--universe-file CSV` | Extra symbols to keep current: an NSE constituent CSV, `Series` = EQ rows only. Default: the CATALAN frozen universe `catalan/data/universe/nifty500_2026-10-18.csv`, if present. It is also the seed candidate list. |
| `--seed` | Seed mode (below). |
| `--seed-from YYYY-MM-DD` | First date to seed. Default `2026-08-01`. |
| `--out-dir DIR` | Where run artefacts go. Default `data/archive/price_gap_backfill/<timestamp>/`, which is gitignored. |

Each run leaves `angel_job.json`, `angel_candles.json` (the raw fetch) and
`report.json` (every decision plus the planned rows) in its run directory. An
`--apply` run also leaves `write_result.json`.

Exit codes:

- `0`: ok.
- `1` with a `STOP:` line: the run halted and nothing further was written. The reason is printed.
- `4`: rows were written but readback found mismatches, which are listed.

## Scope

**Extend mode** covers three groups of symbols:

- Every symbol with a `price_history` row in the table's **last 20 sessions** (recently active).
- **Plus** every current point-in-time NIFTY500 member (`index_membership`, as of the target date) that has any stored history.
- **Plus** every EQ symbol in `--universe-file` that has any stored history.

Symbols with **no** stored history are listed, and the list points to `--seed`.
Delisted names drop out of scope by themselves once they stop trading.

**Seed mode** covers `--universe-file` EQ symbols with no stored rows, minus
three kinds that are excluded by rule and listed:

- `held_back` symbols.
- `alias`: the NSE or Angel name of a symbol already stored under another name. **HEGAM** is the new ticker of stored `HEG`; seeding it would start a second series.
- `placeholder`: NSE `DUMMY*` constituents (ISIN `DUM…`), which stand in for a demerged entity until it lists, e.g. **DUMMYHEG**.

A seed is a short window, not a full history. For a 15-year history, use
`scripts/backfill-angel-15y.mjs`.

## What it checks, in order

| # | Check | On failure |
|---|---|---|
| 1 | **Calendar.** The NSE holiday list is fetched live, merged with the bundled `nse_holidays.json`, and any disagreement is printed. A non-weekday date (Muhurat, budget-Sunday special sessions) counts as a session if ≥ 50% of symbols have a bar. A listed weekday on which < 10% of symbols traded is treated as an unannounced holiday. | Printed under `calendar` |
| 2 | **Token resolution.** `<SYM>-EQ` in Angel's scrip master, which is downloaded into memory on every run. Explicit mappings come from `symbol_overrides.json`. | Symbol skipped (`unresolved`), with candidate instruments of the same name |
| 3 | **Hold-back list.** `hold_back` in `symbol_overrides.json`. | Symbol skipped (`held_back`); the current overlap result is still shown |
| 4 | **Overlap.** The symbol's last 5 stored sessions are re-fetched and compared field by field (0.1% tolerance). | See below |
| 5 | **Corporate action inside the fill window** (NSE corporate-actions feed: split, bonus, demerger, consolidation, capital reduction, scheme, amalgamation). | Symbol skipped (`corporate_action_in_window`) |
| 6 | **Bars.** Exactly the expected sessions, with no duplicates, no bars on non-session dates, no zero or negative prices, no high below max(open, close) or low above min(open, close), and no flat zero-volume filler bars. | Symbol skipped (`validation`) |
| 7 | **Official price.** On the target session plus 2 sampled sessions, Angel OHLC and volume must equal the NSE bhavcopy exactly. Indices are not checked. | Symbol skipped (`official_price_mismatch`) |
| 8 | **Writes (`--apply` only).** Rows already stored are excluded first. The **first batch is a single symbol**, which is read back and compared. Only then does the rest go in, 500 per request. Any Turso quota, limit or permission response, or any statement error, **stops the run**. Transient network errors retry, which is safe because the insert is `INSERT OR IGNORE`. | `STOP:` |
| 9 | **Readback.** Every written row is read back and compared with what was fetched. | Exit 4, mismatches listed |

Daily moves over 18% are listed for review but do not block. Circuit limits and
news days are normal. Every one seen so far matched the official bhavcopy.

## Seed mode: what is checked instead of the overlap

A seed has nothing stored, so there is nothing to overlap. Its identity and
price basis are proven against NSE instead. Steps 1, 2, 6, 8 and 9 above apply
unchanged: calendar, token resolution, bars, first-batch quota stop, and
readback. Then:

| Check | On failure |
|---|---|
| Exclusions: `held_back`, `alias`, `placeholder` (see Scope). | Listed, never fetched |
| **No corporate action anywhere in `[--seed-from, target]`.** A split or bonus inside the window means the bars before the ex-date are on another basis, raw or back-adjusted. | `corporate_action_in_window`. The message gives the fix: `--seed --symbols SYM --seed-from <first session after the ex-date>` |
| The CA feed must be readable for `--apply` (C1, as in extend mode). | `STOP:` |
| **Official price.** On 3 dates (the target plus 2 spread over the window), every Angel bar must equal the NSE bhavcopy exactly. That needs at least 2 matches, or 1 if the stock listed late in the window. Any differing value, or an Angel bar with no NSE row, fails. Pre-ex-date bars that Angel back-adjusted would differ here. | `official_price_mismatch` / `unverified` |
| **Complete from its first bar.** If NSE's bhavcopy lists the stock on a checked date before Angel's first bar, Angel is missing history. | `seed_incomplete` |
| Sessions are checked from the seed's **first bar**, so a stock that listed inside the window passes and is flagged `listed inside the window`. Seeds with under 20 sessions are flagged `SHORT`. | Report only |
| At `--apply` time each seed must **still** have no rows. A seed that gained rows since the fetch is dropped, and extend mode should handle it. | Dropped, listed |

## "Skipped for corporate action": what it means

`[corporate_action] consistent ratio 0.66667 on all 5 overlap days`

Angel **back-adjusts** its whole history when a split, bonus or demerger goes
ex. Our stored rows were written before that, so they are on the old basis.
Re-fetch a stored day and Angel now returns a value that differs from the
stored one by the same factor every day. The factor is the adjustment ratio:
1:2 bonus → 2/3, 1:3 bonus → 3/4, 1:5 split → 1/5.

Appending Angel's new bars would join two bases. The first new day would show a
fake −33% (or −25%, −64%, …) return that never happened, and every momentum,
return or drift calculation downstream would pick it up. A gap is visible and
harmless; a seam is silent and corrupting. So the tool writes **nothing** for
that symbol and prints the ratio and the matching NSE action.

To fix such a symbol, its **entire** history must be rewritten on the adjusted
basis: delete its rows and re-insert from `backfill-angel-15y.mjs`, which costs
about 3,800 writes per symbol. That is deliberately outside this tool, because
it needs DELETEs, and it is a human decision. After a rewrite, the next dry run
shows `overlap exact` for the symbol.

`corporate_action_in_window` is the other case: the ex-date falls inside the
new bars and Angel has **not** back-adjusted yet. Writing would put an
unadjusted jump at the ex-date, so the symbol is skipped and re-evaluated on the
next run.

## Held back: TRENT, CUB, HEG, NATCOPHARM, OLAELEC

These are listed in `hold_back` in `symbol_overrides.json`. The tool
skips them **unconditionally**, even if some future overlap check were to pass,
until the entry is removed.

| Stored symbol | Stored through | Ratio | Action |
|---|---|---|---|
| TRENT | 2026-05-08 | 0.66667 | Bonus 1:2, ex 2026-06-04 |
| CUB | 2026-06-02 | 0.75002 | Bonus 1:3, ex 2026-06-12 |
| HEG (now HEGAM) | 2026-06-02 | 0.35702 | Demerger, ex 2026-09-07 |
| NATCOPHARM | 2026-09-25 | 0.99185 | Rights issue (NSE CA feed 2026-10-01) |
| OLAELEC | 2026-09-25 | 0.982 | Rights issue (NSE CA feed 2026-10-13) |

**The CATALAN pre-registration excludes TRENT, CUB and HEG from its study
universe unless their history is rewritten on a consistent basis.** This tool
upholds that by never extending them. NATCOPHARM and OLAELEC (added
2026-10-23) are in that universe; with no bars after 2026-09-25 they drop out
of its panel mechanically. A rights-issue ratio this close to 1 is still a
seam: joining raw bars onto back-adjusted ones would put a fake ~1-2% move on
the join day. Remove a `hold_back` entry only after the full
rewrite described above.

## Known quirks

- **`fetchDailyOHLC` from-date bug** (`src/services/angelOneHistorical.js`). It
  sends `fromdate` as `YYYY-MM-DD 09:15`, and Angel then drops that day's candle,
  which is stamped 00:00. For example, asking for 09-23 → 09-25 returns only
  09-24 and 09-25.
  - This tool uses its own fetcher (`angel_fetch.mjs`) with `00:00`, starts 7
    days early, and requires every overlap day to come back.
  - The repo helper is **not** fixed. It affects the fund-instrument gap-fill
    in `src/services/syncOrchestrator.js`, which starts the day after the last
    bar and so skips one session per run.
- **Provisional same-evening candles.** Bars fetched from Angel on the evening of
  the session have carried last-traded prices instead of the official close,
  and short volume. 43 stored bars from the August 2026 bulk backfill are like
  this: 34 on 2026-08-03 and 9 on 2026-08-04, off by up to 2.2%. They include
  DOMS, INFY, TATASTEEL and ETERNAL. They were left as stored because correcting
  them needs UPDATEs. Hence the default target of "before today" and the
  bhavcopy check. A differing *last* stored bar (≤ 3%) is tolerated by the
  overlap check for this reason.
- **TRIVENI.**
  - Its demerger went ex on 2026-07-22. Angel had not back-adjusted when the gap
    was filled, so the stored bars up to 2026-07-21 are on the pre-demerger
    basis. There is also a hole from 07-22 to 08-03 (9 sessions), and the bars
    from 08-04 onward are post-demerger.
  - The stored series therefore shows a **fake −48.3%** between 2026-07-21
    (471.50) and 2026-08-04 (243.91). Mask that return downstream.
  - It is not held back: new bars join cleanly at its latest bar.
  - If Angel later back-adjusts, the overlap check will not notice. Only the
    last 5 stored sessions are compared, and they are post-demerger. The fix is
    the same full rewrite as for held-back symbols.
  - For contrast, stored RELIANCE *is* adjusted for the 2023 JIOFIN demerger, so
    the table's convention is adjusted and TRIVENI is the exception.
- **HFCL** moved from EQ to the BE (trade-for-trade) series in September 2026 and
  is no longer resolvable as `HFCL-EQ`. It is mapped to `HFCL-BE` in
  `symbol_overrides.json`. That token serves continuous history and was checked
  against the bhavcopy. Remove the override if HFCL returns to EQ.
- **HEG → HEGAM.** The ticker was renamed; rows stay stored as `HEG`. It is mapped
  to `HEGAM-EQ`, and the bhavcopy check looks for `HEGAM`. It is also held back
  (above).
- **Special sessions.** Stored history includes Muhurat and special weekend
  sessions, for example 2023-11-12 (Sunday), 2025-10-21, and the 2026-02-01
  budget Sunday. The next one is **2026-11-08 (Sunday, Muhurat)**. It is flagged
  in `nse_holidays.json` and accepted automatically when the cross-section
  traded.
- **Holiday list.** NSE's API returns the current year only. `nse_holidays.json`
  bundles 2026. For a run spanning a year NSE no longer returns, the tool infers
  sessions from the cross-section and prints a warning. Add the new year's list
  to the file each January if you want the explicit check.
- **Indices** (`^NSEI`, `^BSESN`) are stored with volume 0 by convention and are
  not in the bhavcopy.
- **Former NIFTY500 members with old gaps** are outside scope: GSPL (data ends
  2025-06-30) and JBCHEPHARM (ends 2023-09-15).
- **`data/angel_scrip_master.json`** is left alone. `angelScripMaster.getToken()`
  rewrites that tracked file when its cache is over 24h old, so this tool
  resolves tokens from an in-memory download instead.
- **TDPOWERSYS** (universe member, no stored rows) had a 1:2 split, ex
  2026-08-24, inside the default seed window, so a default `--seed` skips it.
  It was seeded on 2026-10-23 with `--seed --symbols TDPOWERSYS --seed-from
  2026-08-25` (40 sessions); that window starts after the ex-date.
- **JSWDULUX** had a 1:10 face-value split, ex 2026-10-22. Angel back-adjusted
  its history, so extend skips it (`corporate_action`, ratio 0.1) from
  2026-09-25 on. It is outside the CATALAN universe.
- **BE-series universe rows** (`HFCL`, `MTARTECH`, `STLTECH` in the 2026-10-18
  file) are not read from the universe file, which is EQ only. HFCL is extended
  anyway because it is recently active. MTARTECH (stored through 2026-06-24)
  and STLTECH (through 2026-05-13) are stale and outside scope; they are also
  outside CATALAN's EQ-only universe.
- **Network loss.** If 3 symbols in a row fail with no HTTP response
  (DNS/offline), the fetch aborts and nothing is written. Other per-symbol
  fetch errors show up as `fetch_error` skips; re-running picks them up.
- **Angel throttling (HTTP 403/429).**
  - Seen on 2026-10-18: after about 120 requests, Angel answered 403 for every
    symbol for a few minutes.
  - The fetcher treats a 403/429 that is not a session error as throttling. It
    cools down (10s, 30s, 60s), slows its pace by 50% (to at most 2s/request)
    and retries. If 5 symbols in a row are still refused, it aborts with
    "retry later".
  - It re-logs in only on a real session error (401, or AB1010/AB1004/AB8050).
    Re-logging in on every 403 turned one throttle into a burst of logins.
  - An aborted fetch writes nothing, so just re-run later.

## Adding a symbol mapping

A stored symbol that shows up as `unresolved` has usually been renamed or moved
series. The report lists same-name instruments. Add an entry under `overrides`:

```json
"OLDSYM": {"angel_symbol": "NEWSYM-EQ", "exchange": "NSE", "nse_symbol": "NEWSYM",
           "token_seen": "12345", "added": "YYYY-MM-DD", "reason": "..."}
```

Then run a dry run. The overlap check is what proves the new instrument is the
same series. If it reports a ratio, the rename came with a corporate action;
handle it like a hold-back.

## Files

| File | Role |
|---|---|
| `backfill.py` | Entry point: scope, checks, report, writes. |
| `angel_fetch.mjs` | Angel candle fetcher, run by `backfill.py`. Read-only; reuses `src/services/angelOneAuth.js`. |
| `symbol_overrides.json` | Explicit symbol → Angel instrument mappings, and the `hold_back` list. |
| `nse_holidays.json` | Bundled NSE CM trading holidays; `*` marks a Muhurat session. |
| `test_backfill.py` | Unit tests for the decision logic, extend and seed: `python3 -m pytest scripts/price_gap_backfill/` |

History: built 2026-09-29 from the two manual fills of 2026-09-27. The first
covered 453 symbols, 2026-08-04 → 2026-09-25 (16,976 rows). The second covered
272 stale NIFTY500 members from 2026-05/06 → 2026-09-25 (25,072 rows), with
TRENT, CUB and HEG withheld.

2026-10-23: extend wrote 12,372 rows (723 symbols) through 2026-10-22, and seed
wrote 880 rows (15 + TDPOWERSYS). Every readback matched; the bhavcopy check
was 2,163/2,163 (extend) and 48/48 (seed).
