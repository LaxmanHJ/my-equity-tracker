# CPCV + DSR/PBO — ship/no-ship gate (2026-06-04)

Decisive statistic for putting live capital behind any track. Run via
`python -m quant_engine.ml.cpcv_diagnostic [--pit]`, n_groups=6, n_test_groups=2
(15 combinations), n_trials(DSR)=100. **Ship gate: DSR > 0.95 AND PBO < 0.15.**

NOTE: the PIT run's JSON was overwritten by the legacy run (cp glitch); these
tables are transcribed from the run log. Re-run `--pit` to regenerate the JSON.

## PIT — honest, survivorship-free universe (n=394,518)

| track          | LO Sharpe | LS Sharpe | DSR(LO) | DSR(LS) |
|----------------|-----------|-----------|---------|---------|
| ml             | 0.800     | 0.564     | 0.000   | 0.000   |
| linear         | 0.842     | 0.387     | 0.015   | 0.000   |
| ml_regression  | 0.855     | 0.595     | 0.348   | 0.000   |

PBO (long-short, across tracks): **0.585**

## Legacy — survivorship-biased universe (n=591,308)

| track          | LO Sharpe | LS Sharpe | DSR(LO) | DSR(LS) |
|----------------|-----------|-----------|---------|---------|
| ml             | 1.288     | 0.966     | 1.000   | 0.821   |
| linear         | 1.081     | 0.152     | 0.648   | 0.000   |
| ml_regression  | 1.379     | 0.692     | 1.000   | 0.002   |

PBO (long-short, across tracks): **0.115**

## Verdict

**No shippable edge on the honest universe.** Every PIT track fails both DSR
gates and PBO is 0.585 (catastrophic overfitting). The legacy panel shows the
same tracks "passing" (DSR(LO)=1.0, PBO=0.115) — survivorship bias manufactured
the entire apparent edge. Even market-neutral fails (all LS DSR = 0.000 under
PIT); under PIT LO Sharpe > LS Sharpe, so the long-only "performance" is market
beta, not skill.

n_trials=100 is conservative — the true session OOS-evaluation count is higher
(2 diagnostics + 8 meta-labeler runs + 2 CPCV), which would only lower DSR
further. Decision: scrap the live-capital plan; the surviving signal is, at
best, weak market beta. Keep researching.
