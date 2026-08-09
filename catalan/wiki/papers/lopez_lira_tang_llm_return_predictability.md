# Can ChatGPT forecast stock price movements? Return predictability and large language models

Lopez-Lira & Tang, *Journal of Financial Economics* 184 (2026) 104335.
PDF: `~/Desktop/Proyectos/Scilian-Books/Catalan/1-s2.0-S0304405X26001066-main.pdf`

> **Page status: partial.** The numbers below are the ones CATALAN's design
> depends on. The full method section, the intraday analysis, and the robustness
> battery have not been transcribed yet — read the PDF and extend this page
> before relying on anything not listed here. In particular **the exact prompt
> text is still untranscribed**, which currently blocks Phase 3
> (`catalan/scoring/prompt.py`).

## Problem

Do large language models extract tradable information from news headlines, or do
they just describe what already happened? Prior sentiment work relied on
dictionaries and small supervised classifiers, which do poorly on the compressed,
idiomatic language of headlines.

## Method

- GPT-4 scores a headline as positive / negative / neutral for the named company,
  temperature 0, with a one-sentence rationale.
- Headlines restricted to those released **after the model's training cutoff** —
  this is the design's load-bearing assumption. Without it, the model is
  recalling, not forecasting.
- Relevance filtered to 100 (RavenPack); `stock-gain` / `stock-loss` categories
  dropped; near-duplicate headlines removed by Damerau–Levenshtein similarity
  above 0.6 within symbol-day.
- Long-short daily portfolios formed on the score.

## Key numbers

| Result | Value |
|---|---|
| Daily long-short hit rate, **initial reaction** to overnight news | **93.3%** |
| Drift, gross | **34 bps/day** |
| Annualized Sharpe, gross | **2.97** |
| Round-trip cost at which the strategy dies | **20 bps** |
| Turnover | **~190%/day** |
| Short-leg contribution | **26 bps/day** vs 8 bps for the long leg |
| Partial rebalancing (§6.2), 25% | turnover 190% → 46%/day, **improved** net Sharpe at 10 bps |

## The claim, in two parts

1. **Comprehension.** GPT-4's scores align with the market's *immediate*
   reaction — the 93.3% figure. This is **not tradable**: the reaction happens
   at the open, before which the news has already printed.
2. **Drift.** Prices keep moving in the model's direction for one to two days.
   This is the entire economic content of the paper. It is strongest in small
   caps and on negative news, and it dies at 20 bps round trip.

Reading only the 93.3% and calling it a trading result is the paper's most
obvious misreading, and the one CATALAN's C0/C1/C2 split exists to prevent.

## §7 — model size

Financial reasoning over headlines is presented as an **emergent** capacity:
larger models score better; dictionary methods do not work. CATALAN replicates
this with Haiku 4.5 / Sonnet 5 / Opus 5 plus Loughran–McDonald, which doubles as
the contamination control since the arms have different training cutoffs.

## Online Appendix C — contamination

Tests whether the model scores better on pre-cutoff than post-cutoff headlines,
holding the category mix fixed. A pre-cutoff advantage measures memorisation.

## Project Usage

CATALAN (`catalan/`) runs the same instrument on NSE equities. What transfers,
and what does not:

| Paper | CATALAN |
|---|---|
| RavenPack relevance=100 | NSE `desc` taxonomy (105–122 values); declared exclusion list, frozen before scoring — `config.CATEGORY_EXCLUSIONS` |
| US opening auction, both legs | NSE pre-open auction 09:00–09:15 on entry; **the close is a 15:00–15:30 VWAP, so the exit pays real spread** — the paper's flat-bps justification does not carry |
| Overnight = after US close / before US open | after 15:30 IST on t−1, before 09:00 IST on t — `catalan/data/panel.py::classify_session` |
| Long-short, short leg assumed executable | short leg IS executable intraday (MIS) **except** in T2T / ASM / GSM names, which are flagged and excluded |
| temperature=0 | Opus 5 rejects `temperature` (400); determinism from pinned `(model_id, prompt_hash, effort)` instead |
| Free-text first-line parsing | A/B against structured outputs on 1,000 headlines before adopting structured as primary |
| Post-cutoff headlines | forward log (`data/collect_daily.py`) is the only ship-gate evidence; historical arm measures contamination only |
| Dies at 20 bps | Zerodha cost model declared up front in `PREREGISTRATION.md`, reported per liquidity bucket |

**Gaps.** The intraday arm (paper's 1-min / 15-min windows) cannot run:
`intraday_candles` covers only 16 symbols. Needs an Angel One backfill first.
