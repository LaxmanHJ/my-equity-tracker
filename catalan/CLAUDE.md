# CLAUDE.md — CATALAN

**This file governs all work under `catalan/`.** Where it conflicts with the
repository-root `CLAUDE.md`, this file wins for anything inside this directory.

## What CATALAN is

An independent study: does an LLM's reading of Indian corporate-announcement
headlines predict the *next few hours* of stock returns, net of Indian trading
costs? It replicates Lopez-Lira & Tang, *"Can ChatGPT forecast stock price
movements? Return predictability and large language models"*, JFE 184 (2026)
104335 (`~/Desktop/Proyectos/Scilian-Books/Catalan/`) on the NSE.

The paper's tradable claim is **drift**, not the initial reaction: prices keep
moving in the LLM's direction for 1–2 days, 34 bps/day gross, and the strategy
**dies at 20 bps round-trip cost**. Everything here is built to find out whether
the Indian version survives its own cost model.

## What CATALAN is NOT

It does **not** build on, extend, validate, or reuse conclusions from the
Sicilian engine, the fund, R2, or any other work in this repo. Do not import
their findings as priors and do not cite their results in CATALAN write-ups.
Treat them as a different project that happens to share a disk.

The one exception is **infrastructure**, listed below.

## Boundaries — hard rules

| Rule | Why |
|---|---|
| **Never write to Turso.** Reads only, via `catalan.data.store.turso_ro()`. | Free-tier write quota exhausted 2026-08-05. `tests/test_no_turso_writes.py` enforces it. |
| **The system of record is `catalan/data/forward_log/` (committed NDJSON), not the database.** `catalan.db` is a rebuildable cache. | CI runners are ephemeral and Turso is blocked. More importantly, git commit timestamps are tamper-evident proof a headline was recorded *before* its outcome was known — the evidence the whole forward-log design exists to produce. |
| Never rewrite or reorder archive rows. Append only. | It is evidence. `test_archive.py` asserts a repeat export is byte-identical. |
| Scoring is **cross-sectional**. No function in `scoring/` may take a symbol filter. | The study scores the whole corpus every run; per-symbol scoring would introduce selection the pre-registration does not declare. |
| Own port: **5100**. Never touch 3000 (Node) or 5001 (quant engine). | Runs alongside, not inside. |
| Own UI: `catalan/ui/`, served by CATALAN's own FastAPI. Do not add pages to `public/`. | The main dashboard is a different product. |
| Nothing under `catalan/` may be imported by `src/` or `quant_engine/`. Dependency arrow points one way only. | Deleting `catalan/` must never break the main app. |
| Config constants in `config.py` are **frozen study parameters**. Changing one after scoring begins invalidates the run. | Pre-registration integrity. |

## Infrastructure CATALAN may reuse (do not rebuild)

| Asset | Import path |
|---|---|
| Daily OHLC, 849 symbols, 2011→2026 | Turso `price_history` (read-only) |
| NIFTY500 point-in-time roster | `quant_engine.data.membership.MembershipRegistry` |
| Turso connection | `quant_engine.data.turso_client.connect()` |
| NSE session warm-up + quarter chunking + `INSERT OR IGNORE` | pattern in `quant_engine/data/backfill_earnings.py` |
| Earnings timestamps (event-window control) | Turso `earnings_events` |
| Anthropic batching / error-logging idiom | `quant_engine/sentiment/scorer.py` |

Wrap these behind `catalan/data/universe.py` and `catalan/data/store.py` rather
than importing them all over the package — one seam to swap if the main repo
moves.

## Sequencing — non-negotiable

1. **`PREREGISTRATION.md` is committed before the first scoring run.** No
   exceptions. Declaring the bar before seeing the number is the whole point.
2. **C2 (drift net of costs) is the only ship gate.** A C1 gross result is not
   a result. If you find yourself reporting a gross number without its net
   companion, stop.
3. Score rows are append-only. Re-scoring under a changed prompt writes new
   rows stamped `(model_id, prompt_hash, effort, run_id)` — it never overwrites.

## Memory

CATALAN keeps its own Claude memory, separate from the main project's. It keys
off the working directory, so **run Claude Code with `catalan/` as cwd** when
doing CATALAN work:

```
/Users/elj/.claude/projects/-Users-elj-Desktop-Proyectos-PersonalStockAnalyser-catalan/memory/
```

Do not write CATALAN findings into the main project's memory, and do not carry
main-project memories (R2, fund, Sicilian, ML) in as context here.

## Wiki

CATALAN keeps its literature notes in `catalan/wiki/`, not the repo-root
`wiki/`. Same discipline as the root project: read before implementing, update
after. Index new pages in `catalan/wiki/README.md`.

## Commands

```bash
# Serve API + UI on 5100
python3 -m uvicorn catalan.main:app --host 0.0.0.0 --port 5100 --reload

# THE DAILY PIPELINE — what the GitHub Action runs.
# rebuild → collect → score (whole cross-section) → archive
python3 -m catalan.pipeline                        # yesterday
python3 -m catalan.pipeline --date 2026-08-07
python3 -m catalan.pipeline --skip-score           # no Anthropic calls

# Individual stages
python3 -m catalan.data.collect_daily --date <YYYY-MM-DD>
python3 -m catalan.scoring.scorer --pending                    # sync, fast
python3 -m catalan.scoring.scorer --pending --transport batch  # 50% cheaper, ≤24h
python3 -m catalan.scoring.scorer --collect                    # gather batches
python3 -m catalan.scoring.scorer --pending --dry-run          # count, call nothing

# Manual entry — the escape hatch for news the NSE feed missed
python3 -m catalan.scoring.scorer --headline "..." --company "..." [--symbol RELIANCE]

# Archive
python3 -m catalan.data.archive --rebuild          # restore db from git
zcat catalan/data/forward_log/announcements/2026-08-07.ndjson.gz | head

# Tests
python3 -m pytest catalan/tests/
```

**Transport choice is a deadline decision, not a preference.** The daily run uses
`sync` because overnight news must be scored between the 09:00 IST cutoff and
the 09:15 open, and the Batch API's up-to-24h SLA cannot be trusted to meet
that. At ~900 headlines/day on Haiku the cost of choosing speed is cents. Use
`batch` only for the historical backfill, where nothing waits on the answer.
