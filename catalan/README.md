# CATALAN

LLM news-reaction study on Indian equities. Standalone project; shares this
repo's data infrastructure and nothing else.

**Question.** Do Claude's readings of NSE corporate announcements predict the
same-session drift in the announcing stock — and does that edge survive Indian
round-trip trading costs?

**Source.** Lopez-Lira & Tang, *"Can ChatGPT forecast stock price movements?
Return predictability and large language models"*, JFE 184 (2026) 104335.
US result: 34 bps/day gross on the drift leg, 2.97 annualized Sharpe, dies at
20 bps round-trip.

## Status

Collection and scoring are **live and automated**. The daily pipeline runs in
GitHub Actions and commits its results back to the branch. The returns panel and
all analysis (Phases 2 and 4) are still stubs, so no gate has been evaluated.

`PREREGISTRATION.md` is still a draft — every threshold is `TBD` and it must be
finalized before any result is computed. Scoring may proceed meanwhile: the
corpus and its verdicts are inputs, and the archive's git timestamps are what
make the pre-registration credible when it is finalized.

## Layout

```
catalan/
├── CLAUDE.md            # rules for working in here — read first
├── PREREGISTRATION.md   # the declared bar; finalize BEFORE computing any gate
├── config.py            # frozen study constants
├── pipeline.py          # the daily run: rebuild → collect → score → archive
├── main.py              # FastAPI :5100, serves the API and the UI
├── data/
│   ├── collect_daily.py # the forward log
│   ├── archive.py       # the git-committed NDJSON system of record
│   ├── forward_log/     # ← the actual evidence, committed
│   ├── universe.py      # PIT NIFTY500
│   └── panel.py         # returns panel (stub)
├── scoring/             # frozen prompt, sync + batch transports, filters
├── analysis/            # portfolio, regressions, costs, contamination (stubs)
├── routers/             # HTTP surface
├── ui/                  # CATALAN's own dashboard (not public/)
├── wiki/                # CATALAN's literature notes
└── tests/               # 93 tests, all offline
```

## How scoring works

**Automated and cross-sectional.** `score_pending()` selects *every* unscored
announcement in the corpus — it takes no symbol argument at all — applies the
declared filters, and scores the lot. A typical NSE day is ~1,300 announcements
across ~660 symbols, which the filters reduce to ~900 worth paying for
(~170 excluded categories, ~215 near-duplicates). That takes about three minutes
on Haiku 4.5.

Anything that abstained or failed on an earlier run stays pending and is retried
automatically on the next one, so transient API failures self-heal.

**Manual entry** exists for news the NSE feed did not carry — `POST
/api/study/score`, the form on the dashboard, or `--headline` on the CLI.
Persisted manual rows are stamped `source='manual'`, because a manual headline
has no exchange timestamp guarantee and must stay separable from the forward log.

## The archive is the system of record

CI runners are ephemeral and Turso writes are blocked, so `catalan.db` cannot be
authoritative. Everything durable lives in gzipped NDJSON under
`catalan/data/forward_log/`, committed to the branch by the scheduled workflow
(~69 KB/day, ~17 MB/year).

That is not just a storage workaround. **A git commit timestamp is independent,
tamper-evident proof that a headline was recorded before the return it predicts
was known** — exactly the evidence the forward log exists to produce, and much
stronger than a row in a database the researcher can edit.

```bash
python3 -m catalan.data.archive --rebuild   # restore the db from git alone
```

## Run

```bash
python3 -m pip install -r catalan/requirements.txt

# dashboard
python3 -m uvicorn catalan.main:app --host 0.0.0.0 --port 5100 --reload
open http://localhost:5100/

# the daily pipeline, by hand
python3 -m catalan.pipeline --date 2026-08-07
```

`GET /health` reports port, store path, corpus size and Turso read reachability.

## Automation

`.github/workflows/catalan-forward-log.yml` runs the pipeline at **03:45 UTC
(09:15 IST), Tue–Sat**, collecting the previous calendar day and committing the
archive back to `r2-nifty500-gate`.

The schedule is set by the data, not convenience: NSE announcements keep landing
until ~23:59 IST (the latest row on 2026-08-07 was 23:57), so any earlier run
would truncate the day.

Requires repository secret `ANTHROPIC_API_KEY`. Without it the run still
collects and skips scoring — headlines are the irreplaceable part, and scores
can always be backfilled from them.

A zero-row day fails the run loudly rather than passing green, because a
silently-dead collector is this study's main failure mode.

## Phases

| Phase | What | State |
|---|---|---|
| **1a** | Forward collector, scheduled | **live** — verified on 2026-08-07 |
| 0 | Package, store, `/health`, UI | **done** |
| 3 | Scoring engine — prompt transcribed, sync + batch, resumable, cross-sectional | **live** |
| 1b | Historical ingest 2019→now | stub — exploratory arm only |
| 2 | Returns panel: PIT join, `init_ret`/`drift_ret`, liquidity buckets, shortability | stub |
| 4 | Analysis: portfolio, FE regressions, topics, costs, contamination, model-size arm | stub |
| 5 | Intraday arm — **blocked**, `intraday_candles` covers only 16 symbols | not started |

## Non-negotiables

- Turso is **read-only** from here. Writes are quota-blocked repo-wide.
- C2 (net of costs) is the ship gate. C1 gross alone is not a result.
- Scores are append-only and provenance-stamped `(model_id, prompt_hash, effort, run_id)`.
- The archive is append-only evidence. Never rewrite or reorder it.
