# Short-Horizon Cross-Sectional Signals (1–5d) — 2026-06-10 Experiment

Closes audit open research item **H.1** from
[ml_audit_2026_05_21.md](ml_audit_2026_05_21.md#h-open-research-items-post-audit):
"Alternative horizons (1-3d): cross-sectional momentum / reversal at short
horizons has different signal/noise properties. Cheap test."

## Question

Is there a tradeable **next-day** (1–3d) cross-sectional signal on the NSE
Nifty-200 PIT universe? Context: the 2026-06-04 terminal audit showed the
existing factor stack (linear / RF / ml_regression) has **no 1d edge** —
hit rates 50.0–50.3%, 1d cs-IC ≤ +0.013.

## Method

`quant_engine/research/short_horizon.py` (new). PIT-filtered Nifty-200
(332 symbols ∩ membership registry), daily-cadence rows only (trailing
10-bar median gap ≤ 1.6 days kills the legacy weekly-bar era), 2016-01 →
2026-06, 411k rows, 2,092 dates. Six **declared-up-front** candidates ×
4 horizons = 24 trials; under the null the expected max |t| ≈ 2.9, so the
evidence bar was |t| > 3 AND positive net-of-cost Sharpe.

## Results (2026-06-10, `data/short_horizon_experiment.json`)

Cross-sectional Spearman IC (overlap-adjusted t):

| Signal | 1d IC / t | 2d | 3d | 5d |
|---|---|---|---|---|
| rev1 (−ret_1d) | +0.0125 / **4.2** | +0.0199 / 5.0 | +0.0192 / 4.0 | +0.0157 / 2.5 |
| **rev3 (−ret_3d)** | +0.0195 / **6.3** | +0.0222 / 5.1 | +0.0218 / 4.2 | +0.0231 / 3.4 |
| rev5 (−ret_5d) | +0.0163 / 5.2 | +0.0217 / 4.9 | +0.0226 / 4.2 | +0.0233 / 3.4 |
| rev5_vs (vol-scaled) | +0.0149 / 5.2 | +0.0197 / 4.9 | +0.0204 / 4.2 | +0.0201 / 3.2 |
| mom21_skip5 | +0.0031 / 1.0 | — | — | — (n.s.) |
| lowvol | +0.0177 / 4.7 | +0.0164 / 3.1 | +0.0155 / 2.4 | +0.0153 / 1.8 |

Daily-rebalanced top-minus-bottom quintile LS at fwd1
(gross / net@15bp / net@30bp annualized Sharpe; turnover):

| Signal | Gross | Net 15bp | Net 30bp | Turnover/day |
|---|---|---|---|---|
| rev1 | **−1.18** | −5.41 | −9.61 | 78% |
| rev3 | +0.35 | −2.01 | −4.37 | 45% |
| rev5 | +0.10 | −1.71 | −3.53 | 35% |
| rev5_vs | +0.20 | −1.89 | −3.97 | 36% |
| mom21_skip5 | +0.08 | −1.02 | −2.11 | 20% |
| lowvol | +0.07 | −0.34 | −0.76 | 9% |

## Findings

1. **Short-term reversal is statistically real on NSE** — rev3 at t = 6.3 is
   the strongest cross-sectional signal ever measured in this project on
   honest PIT data (vs linear composite's sub-significant +0.003 at 20d).
2. **Nothing survives costs.** Best gross LS Sharpe is +0.35 (rev3) ≈ 4–5%
   gross/yr; daily turnover of 35–78% × 15–30bp one-way costs is a
   10–25%/yr drag. Every net cell is negative. This matches production
   reality: short-term reversal is a market-maker / sub-5bp-cost strategy.
3. **Tail continuation, not reversal**: rev1's quintile LS is *negative
   gross* (−1.18) while its full-cross-section IC is positive — extreme
   1-day movers on NSE continue (circuit-limit dynamics, news drift),
   while the middle of the distribution mean-reverts. Quintile portfolios
   trade exactly the tails, hence the sign flip. Flipping rev1's tails
   into a 1d "extreme continuation" long-short is +1.18 gross — but its
   78% turnover still kills it net.

## Verdict for next-day prediction

**A per-stock next-day BUY/SELL/HOLD with positive net expectancy is not
achievable** with daily price/volume signal classes at retail Indian
cash-equity costs. The signal exists; the costs eat it. The project's
realistic edge horizon remains 2–4 weeks where turnover amortizes.

Possible follow-ups (each must clear CPCV+DSR, PIT, net-of-cost):
- Use rev3 as a **timing overlay on entries the 20d engine already wants
  to make** (zero incremental turnover → cost-free IC capture).
- Intraday execution variants (costs ~5-10bp) — needs F&O or intraday
  infrastructure, different project.

## 2026-06-10 Follow-up: Entry-Timing Overlay — TESTED, FAILED, CLOSED

The "rev3 as cost-free entry timing" idea was tested the same day via a
paired event study (`quant_engine/research/entry_timing.py`, results in
`data/entry_timing_experiment.json`). Declared rule: on a linear-LONG
crossing (composite ≥ +0.40), delay entry up to K=3 days waiting for
rev3 cross-sectional percentile ≥ 0.5; common exit at t0+21 for both arms.

| Cohort | n | Mean diff | t (monthly-clustered) | Hit improved |
|---|---:|---:|---:|---:|
| signal (LONG crossings) | 293 | **−54 bp/trade** | −1.21 | 32.1% |
| unconditional (every 5th day) | 79,052 | −5.1 bp | **−2.08** | 25.9% |

**Verdict: do NOT ship.** Waiting for a pullback after a momentum signal
costs more drift than the better entry recovers — post-signal continuation
(the same tail-momentum effect rev1's quintile LS exposed) dominates. Worst
in trending recoveries (2020: −402 bp). Per-exposure-day returns are
identical between arms (~6.6 bp/day), i.e. the timing adds nothing even
risk-adjusted. The next-day reversal IC is real in the cross-section but
cannot be monetized on this book: not standalone (costs), not as overlay
(forfeited drift). **This research thread is closed.**

## Project Usage

- Experiment runners: `quant_engine/research/short_horizon.py`,
  `quant_engine/research/entry_timing.py`
- Outputs: `data/short_horizon_experiment.json`,
  `data/entry_timing_experiment.json`
- Both negative results are binding: do not re-run variants (threshold /
  K sweeps) without a fundamentally different cost or execution story —
  that would be multiple-testing laundering.
