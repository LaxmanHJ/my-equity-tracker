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

## Concepts

| File | Topic |
|------|-------|
| [factor_scoring.md](concepts/factor_scoring.md) | Multi-factor scoring engine — weights, normalization, thresholds |
| [momentum.md](concepts/momentum.md) | Cross-sectional vs. time-series momentum |
| [mean_reversion.md](concepts/mean_reversion.md) | Mean reversion — evidence, signals, regime dependency |
| [regime_detection.md](concepts/regime_detection.md) | Market regime — VIX, Markov, FII flow |
| [ml_pipeline.md](concepts/ml_pipeline.md) | ML training, CV, labeling, features |
| [intraday_features.md](concepts/intraday_features.md) | Angel One 15-min candle features (Phase 4) |
| [backtesting.md](concepts/backtesting.md) | Backtesting methodology, pitfalls, CPCV |
| [covariance_estimation.md](concepts/covariance_estimation.md) | Portfolio risk — shrinkage, HRP |

## Live Trading

| File | Topic |
|------|-------|
| [live_trading_checklist.md](live_trading_checklist.md) | Pre-live gap analysis — 13 items to fix before real capital |

## Wiki Operations

- **Ingest**: Read source PDF → create/update paper page + relevant concept pages
- **Query**: Search wiki for a topic before implementing
- **Update**: After any implementation, add a "Project Usage" section to affected pages
