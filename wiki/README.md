# PersonalStockAnalyser — Research Wiki

LLM-maintained knowledge base (Karpathy wiki pattern). Every paper, concept, and algorithm used or planned in this project lives here. When implementing anything, also update the relevant wiki page.

## Papers

| File | Title | Status |
|------|-------|--------|
| [kakushadze_101_alphas.md](papers/kakushadze_101_alphas.md) | 101 Formulaic Alphas — Kakushadze (2015) | Ingested |
| [jegadeesh_titman_1993.md](papers/jegadeesh_titman_1993.md) | Returns to Buying Winners and Selling Losers — Jegadeesh & Titman (1993) | Ingested |
| [ledoit_wolf_shrinkage_2021.md](papers/ledoit_wolf_shrinkage_2021.md) | Nonlinear Shrinkage of Covariance — Ledoit & Wolf (2021) | Ingested |
| [lopez_de_prado_afml_2018.md](papers/lopez_de_prado_afml_2018.md) | Advances in Financial Machine Learning — López de Prado (2018) | Ingested (ToC + Ch.1) |
| [asness_fact_fiction_momentum_2014.md](papers/asness_fact_fiction_momentum_2014.md) | Fact, Fiction, and Momentum Investing — Asness et al. (2014) | Ingested |
| [asness_value_momentum_everywhere_2013.md](papers/asness_value_momentum_everywhere_2013.md) | Value and Momentum Everywhere — Asness, Moskowitz & Pedersen (2013) | Ingested |
| [hurst_trend_following_century_2017.md](papers/hurst_trend_following_century_2017.md) | A Century of Evidence on Trend-Following — Hurst, Ooi & Pedersen (2017) | Ingested |
| [grinold_kahn_active_portfolio.md](papers/grinold_kahn_active_portfolio.md) | Active Portfolio Management — Grinold & Kahn (1999) | Placeholder |
| [cartea_jaimungal_ahft_2015.md](papers/cartea_jaimungal_ahft_2015.md) | Algorithmic and High-Frequency Trading — Cartea, Jaimungal & Penalva (2015) | Ingested (ToC + Preface) |
| [lebron_laws_of_trading_2019.md](papers/lebron_laws_of_trading_2019.md) | The Laws of Trading — Agustin Lebron (2019) | Ingested |

## Concepts

| File | Topic |
|------|-------|
| [factor_scoring.md](concepts/factor_scoring.md) | Multi-factor scoring engine — weights, normalization, thresholds |
| [momentum.md](concepts/momentum.md) | Cross-sectional vs. time-series momentum |
| [mean_reversion.md](concepts/mean_reversion.md) | Mean reversion — evidence, signals, regime dependency |
| [regime_detection.md](concepts/regime_detection.md) | Market regime — VIX, Markov, FII flow |
| [ml_pipeline.md](concepts/ml_pipeline.md) | ML training, CV, labeling, features |
| [ml_audit_2026_05_21.md](concepts/ml_audit_2026_05_21.md) | ML pipeline audit (2026-05-21) — severity-ranked structural issues, hedge-fund-practice gap analysis, scrap-vs-fix recommendation, prioritized rewrite plan |
| [survivorship_pit_universe.md](concepts/survivorship_pit_universe.md) | Point-in-time index membership — survivorship-bias fix (audit P2-c). Schema, ingest CLI, registry, sourcing procedure |
| [intraday_features.md](concepts/intraday_features.md) | Angel One 15-min candle features (Phase 4) |
| [backtesting.md](concepts/backtesting.md) | Backtesting methodology, pitfalls, CPCV |
| [covariance_estimation.md](concepts/covariance_estimation.md) | Portfolio risk — shrinkage, HRP |
| [claude_final_gate.md](concepts/claude_final_gate.md) | Claude (opus-4-7) as final execution-plan gate before broker (SIC-31) |
| [sentiment.md](concepts/sentiment.md) | News/event sentiment pipeline — sources, scorer chain, sentiment_daily table |
| [short_horizon_reversal.md](concepts/short_horizon_reversal.md) | 1–5d cross-sectional signals (2026-06-10) — reversal real (rev3 t=6.3) but dies on costs; entry-timing overlay also tested and FAILED (−54bp/trade). Closes audit item H.1; thread closed |
| [bulk_deal_drift.md](concepts/bulk_deal_drift.md) | Bulk/block-deal drift + counterparty studies (2026-06-10) — both FAIL; 124k deals backfilled 2019–2026; DESK_SELL curiosity noted but not promoted |
| [delivery_spike.md](concepts/delivery_spike.md) | Delivery-spike events (2026-06-10) — FAIL after PIT: +85bp/t=3.3 collapses to +12bp/t=0.9; survivorship lesson reproduced live. 509k delivery rows backfilled 2019–2026; PIT now mandatory for event studies |
| [pead.md](concepts/pead.md) | PEAD (SIC-93, 2026-06-10) — CLOSED, FAIL: two data extensions (integrated 2025+, legacy 2018) resolved the marginal t AGAINST the long side (2.6→2.9→2.2); effect is regime-dependent. 96k earnings broadcasts 2018→2026 remain as a permanent event table |
| [vol_risk_premium.md](concepts/vol_risk_premium.md) | NIFTY VRP (SIC-92, 2026-06-10) — premium CONFIRMED (t=5.84, 15y incl COVID via Yahoo VIX backfill); naive harvest fails (one 2020 cycle = 11yrs of carry); phase 2 = defined-risk structures, daily chain collection LIVE in Force Sync |
| [fund_design.md](concepts/fund_design.md) | Multi-sleeve fund book (2026-07-25) — beta core + factor fund + VRP + LS pilot; ledger-driven, fund_* tables, GH Actions sync; Study A (monthly 12-1 momentum) FAILED its gate → FACTOR_EQ via Momentum 30 index fund |

## Live Trading

| File | Topic |
|------|-------|
| [live_trading_checklist.md](live_trading_checklist.md) | Pre-live gap analysis — 13 items to fix before real capital |

## Wiki Operations

- **Ingest**: Read source PDF → create/update paper page + relevant concept pages
- **Query**: Search wiki for a topic before implementing
- **Update**: After any implementation, add a "Project Usage" section to affected pages
