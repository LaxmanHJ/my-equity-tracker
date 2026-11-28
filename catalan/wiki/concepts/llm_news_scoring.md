# LLM news scoring

Scoring a news headline with a language model and treating the score as a
cross-sectional return predictor.

## The technique

1. For each headline, ask a model whether it is good or bad for the named
   company's stock price. Map YES → +1, UNKNOWN → 0, NO → −1.
2. Restrict to announcements the market could not yet have traded on.
3. Form a daily long-short portfolio on the score.
4. Measure two legs separately — the reaction, and the drift that follows it.

## The three things that silently fake a positive result

These are the failure modes CATALAN is engineered around. Any implementation
that skips them will produce a large, clean, wrong number.

### 1. Lookahead via memorisation

The design rests entirely on scoring headlines released *after* the model's
training cutoff. Score a 2019 headline with a 2026-cutoff model and it is
recalling the outcome, not forecasting it — and nothing in the output reveals
this. It looks like skill.

**Mitigation:** a forward log that accrues one day per calendar day and is the
only evidence allowed to clear the ship gate; plus an explicit pre/post-cutoff
comparison, category mix held fixed, to quantify how much the historical arm's
edge is recall (`catalan/analysis/contamination.py`).

The forward log's cost is that it cannot be hurried. This is why the collector
ships before anything else.

### 2. Costs

The source paper's own strategy is unprofitable at 20 bps round trip on ~190%
daily turnover. A gross result is therefore nearly uninformative about whether
the strategy is real.

**Mitigation:** the cost model is declared *before* the result is seen, and
spread/impact is estimated per liquidity bucket rather than as one flat number —
because the alpha concentrates in small caps, which is exactly where impact is
worst. If the edge survives only in the least liquid bucket, that is recorded in
advance as a negative result.

### 3. Survivorship and entity linkage

Filtering against today's index roster drops the firms that left because they
failed. Guessing a ticker from a company name in free text creates spurious
matches at a rate that is invisible in aggregate statistics.

**Mitigation:** point-in-time membership via `MembershipRegistry`, and a news
spine (NSE corporate announcements) where the exchange itself tags every filing
with `symbol` and `sm_isin` — no name resolution required. This is the main
reason NSE announcements anchor the study and Zerodha Pulse does not: Pulse has
no ticker tag, no company entity, and no archive.

## The NSE text problem — open, and decided by C0

The paper scored **news headlines** that state what happened ("Apple beats
earnings estimates"). NSE's `attchmntText` is a **filing description**, and the
substance lives in an attached PDF. Measured on 2026-08-07:

| | |
|---|---|
| Announcements with a PDF attached | **1,286 / 1,286 (100%)** |
| Text beginning "X has informed/submitted the Exchange…" | 92.6% |
| `attchmntText` length | median 108 chars, max 838 — **not truncated** |

So the instrument is not reading the same object the paper read. The question is
whether the description carries enough signal anyway.

**First evidence that it carries something.** If the model were merely mapping
announcement *type* to a prior, scores would be constant within a `desc`
category. They are not: on n=225, **87% of the total score dispersion survives
inside a category**, and all 7 categories with n≥5 are genuinely mixed. It is
reading the text, not the label.

**But that is not proof it is reading the news.** `Outcome of Board Meeting`
scored 21 NO / 5 YES / 5 UNKNOWN out of 31 — consistent with the model
defaulting negative on text that says "approved financial results" without
saying whether they were good.

**This is settled empirically by C0, not by argument.** C0 compares the score
against the market's own immediate reaction. If text-only scoring cannot beat
chance there, the description is too thin and the PDFs become necessary.

**Why the PDFs are not read today** — a deliberate scoping decision, not an
oversight:

- ~1,286 PDFs/day at ~1.3 MB each is ~1.7 GB/day; it cannot be archived the way
  the text can.
- A document is thousands of tokens against ~180 for a headline — roughly a 50x
  cost increase, moving the daily run from cents to tens of dollars.
- It changes the instrument. The paper's claim is about headlines; scoring full
  documents is a *different* experiment and would have to be pre-registered and
  reported as one, not silently swapped in.

**What is done today:** `attachment_url`, `attachment_size`, `has_xbrl` and
`disseminated_at` are captured on every row and archived. Storing them is free;
they are unrecoverable if NSE rotates its archive. That keeps the PDF experiment
available as a declared arm later without committing to it now.

### Measured at scale — data audit, 2026-09-27 (26,585 records)

Source: `catalan/audit/output/data_audit_report.md`. No returns were read.

- **52% of headlines are label-only.** With the company name removed, the text is identical
  across ≥5 companies: "…about Credit Rating" (291/291), "…the financial results for the period
  ended Jun 30" (969). The model sees nothing company-specific, yet still gives a direction to 54%
  of them. **40% of all YES/NO verdicts were given on label-only headlines.**
- **44% of directional verdicts (a lower bound) have a rationale that says the filing is
  "routine / neutral / no material information"**, and still vote. Mostly NO: the model returns
  NO on 71% of shareholder-meeting filings.
- **The instrument is noisy at default temperature.** The scorer sets no temperature, so Haiku
  runs at 1.0. 1,334 headlines were accidentally scored ~15 times each: 42% changed verdict, and
  222 got both YES and NO for identical input.
- **Correction to the "first evidence" above:** the 87% of dispersion that survives *within* a
  category is not clean evidence of information. Much of it can be sampling noise. That test
  needs temperature 0, or repeated scoring, before it can be read.
- **Results and credit ratings** are the worst headline inputs (64% / 83% label-only) and, per
  the PDF sample, among the best document inputs. So "drop label-only headlines" is right for the
  headline arm and wrong for a PDF arm.
- **The paper's dedup rule does not transfer.** At 0.6 whole-headline similarity, NSE's shared
  template makes unrelated filings look alike: 63% of drops differ in category from their keeper,
  and 18 of 22 hand-read drops were distinct events.

## India adaptations

The US definitions do not transfer unmodified.

| Aspect | NSE specifics |
|---|---|
| Overnight window | after 15:30 IST on t−1 or before 09:00 IST on t. The 09:00 cutoff leaves 15 min before the 09:15 open. IST has no DST — no ambiguous local times. |
| Entry price | NSE runs a pre-open **call auction** 09:00–09:15, so the daily `open` is an auction clearing price — a clean analogue of the paper's opening auction. |
| Exit price | The close is a **VWAP of 15:00–15:30**, *not* an auction. The paper justified flat-bps costs on the grounds that both legs cleared in auctions with no bid-ask spread. **That justification does not hold on the exit here.** |
| Short leg | The drift leg is entirely intraday, so it can be traded MIS and the short leg is executable — which matters, since the paper finds the short leg carries most of the alpha. **But** intraday shorting is banned in T2T and surveillance (ASM/GSM) names, which must be flagged per-day and excluded. |
| Topic taxonomy | NSE supplies 105–122 `desc` values — a vendor taxonomy, better than clustering embeddings, and the natural substitute for RavenPack relevance filtering. |

## Project Usage

Implemented in `catalan/`:

- `data/collect_daily.py` — the forward log (Phase 1a). NSE corporate
  announcements, raw and unfiltered; filters are query-time so the corpus stays
  auditable.
- `data/panel.py::classify_session` — the 09:00 / 15:30 IST boundary logic,
  tested at the exact edges.
- `data/universe.py` — PIT NIFTY500 via the main repo's `MembershipRegistry`.
- `scoring/prompt.py` — frozen prompt + hash. Every score row carries
  `(model_id, prompt_hash, effort, run_id)`; re-scoring appends, never overwrites.
- `analysis/costs.py`, `analysis/contamination.py` — the two mitigations above.
- `PREREGISTRATION.md` — C0 comprehension / C1 gross / **C2 net = ship gate**.
- `data/pdf_text.py` — the PDF arm's input (built 2026-09-27, not yet wired into
  the pipeline): first 3 pages, a readability check (scans and broken font
  maps; Devanagari allowed), all-or-nothing fallback to headline-only, and the
  text archived to `forward_log/pdf_text/`.
- `audit/data_audit.py` — read-only, reproducible audit of the collected corpus
  (2026-09-27). See the section above.

**Open:** the prompt is not yet transcribed from the paper; the `desc` exclusion
list is not yet frozen; the T2T/ASM/GSM source for the shortability flag is not
yet settled, so the short leg cannot currently be claimed as executable.
