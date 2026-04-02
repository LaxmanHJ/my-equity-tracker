
  ---
  1. Cross-Sectional Momentum (High success rate)

  Used by: AQR, Two Sigma, Cliff Asness

  Idea: Every month, rank all 15 stocks by their 3-6 month return. Go long the top 3-5, sit out the rest. No individual stock signals — just relative ranking.

  Why it works: Stocks that outperformed last quarter tend to keep outperforming next quarter (documented in 40+ years of academic research across every market).

  Your data bottleneck: Only 15 stocks. Normally this needs 100+. Smaller universe = less diversification but still workable.

  ---
  2. Regime-Adaptive Strategy (Very high success rate)

  Used by: Renaissance, Bridgewater, Man AHL

  Idea: Don't use one strategy — switch between two based on market regime:
  - Bull regime (VIX low, FII buying, Markov = bullish) → trend-following mode
  - Bear/choppy regime (VIX high, FII selling) → mean reversion mode or cash

  Why it works: Trend-following destroys itself in sideways markets. Mean reversion destroys itself in trending markets. Knowing which regime you're in is worth more than any single signal.

  Your data advantage: You already have VIX, FII flows, and Markov regime — you're closer to this than you think.

  ---
  3. Pure Mean Reversion (Medium-high success rate)

  Used by: DE Shaw, Citadel (stat arb desks)

  Idea: When a stock drops 2+ standard deviations below its 20-day mean, buy. When it reverts back to the mean, sell. Short holding periods (3-10 days).

  Why it works: In the absence of a real fundamental change, stocks tend to snap back. High win rate (60-70%) but small gains per trade.

  Your data: Bollinger bands and z-score already computed in your factors. You essentially already have this — just not wired as a standalone strategy.

  ---
  4. FII Flow Following (India-specific, underused)

  Used by: India-focused hedge funds like Alchemy, DSP

  Idea: FII cash flow is the single most powerful macro signal in Indian markets. When FII net buying crosses a threshold for 3+ consecutive days → go long your strongest momentum stocks. When FII turns net
  seller → exit everything.

  Why it works: FII money moves Indian markets. Retail and domestic institutions react after FIIs move. You have this data already.

  Your data advantage: Most retail backtesting tools don't have this. You do.

  ---
  5. Pairs Trading (Stat Arb)

  Used by: Citadel, Two Sigma

  Idea: Find two highly correlated stocks (e.g., HDFCBANK vs ICICIBANK). When the spread between them diverges beyond normal, go long the underperformer and short the outperformer, betting they'll converge.

  Your data bottleneck: Requires shorting (not available retail in India for overnight positions) and a large enough universe to find stable pairs. Hard with 15 stocks.

  ---
  Realistic priority given your constraints:

  ┌──────────────────────────┬─────────────────┬───────────────────────┬───────────────────────┐
  │         Strategy         │ Effort to build │    Likely success     │ Data you already have │
  ├──────────────────────────┼─────────────────┼───────────────────────┼───────────────────────┤
  │ Regime-Adaptive          │ Medium          │ Very high             │ 90% there             │
  ├──────────────────────────┼─────────────────┼───────────────────────┼───────────────────────┤
  │ Cross-sectional Momentum │ Low             │ High                  │ Yes                   │
  ├──────────────────────────┼─────────────────┼───────────────────────┼───────────────────────┤
  │ FII Flow Following       │ Low             │ High (India-specific) │ Yes                   │
  ├──────────────────────────┼─────────────────┼───────────────────────┼───────────────────────┤
  │ Pure Mean Reversion      │ Low             │ Medium-high           │ Yes                   │
  ├──────────────────────────┼─────────────────┼───────────────────────┼───────────────────────┤
  │ Pairs Trading            │ High            │ Medium                │ Partial               │
  └──────────────────────────┴─────────────────┴───────────────────────┴───────────────────────┘

  ---
  Forward Signal Validation — Comprehensive Research Summary

       This covers everything from precise terminology through hedge fund implementation details, with specific callouts to how your existing code already intersects with this
       topic.

       ---
       1. Precise Terminology

       The practice goes by several overlapping names depending on context:

       "Forward return analysis" is the most common term in academic and practitioner literature. You generate a signal at time t, then measure the return from t to t+N and ask
       whether the signal had predictive power over that horizon. The AQR white papers (Asness et al.) use this framing throughout.

       "Signal decay analysis" or "signal half-life analysis" is the term used when you plot predictive power (IC) as a function of N — i.e., how fast the signal's edge decays as
       the holding period lengthens. This is a derived analysis built on top of forward return analysis.

       "Out-of-sample forward testing" or "walk-forward validation" is the term when you are doing this prospectively (logging today, checking later), as opposed to retrospectively
       (computing it over historical bars). The prospective version is sometimes called "live shadow testing" or "paper trading validation."

       "Point-in-time validation" is a related but distinct concept — it refers to using only the data that was actually available at the time the signal was generated (avoiding
       look-ahead bias). You already handle this correctly: your signals_log table records signals at signal_date, and getSignalsHistory joins against price_history where date >
       signal_date. That is the textbook point-in-time join.

       "Information Coefficient (IC) analysis" is both the method and a specific metric name. Used at Two Sigma, AQR, WorldQuant, and described extensively in Grinold & Kahn's
       "Active Portfolio Management" (the canonical reference).

       The full practitioner framing is: "forward IC analysis on a live signal journal."

       ---
       2. How Hedge Funds Implement This

       Signal logging

       Every major systematic fund maintains a signal journal — a time-stamped log of every signal generated, stored immutably. The key constraints are:

       - Records must be written before the forward period begins — i.e., signal logged at market close on day t, not amended later. This is exactly what your saveSignalsLog does.
       - The log must record the raw score, not just the classified label (LONG/SHORT/HOLD), because IC is computed against the continuous score. Your composite_score column serves
       this purpose.
       - At funds like Two Sigma and AQR, signals are logged to append-only event stores (Kafka, time-series databases). The principle is the same as your SQLite UNIQUE(signal_date,
        symbol) upsert — one canonical record per (stock, date) pair.

       Forward return computation

       Funds typically compute forward returns at multiple horizons simultaneously:

       - 1-day (next open to next close, or close-to-close): immediate reaction
       - 5-day (1 week): short-term drift
       - 20-day (1 month): medium-term signal validity — this is your current default in getSignalsHistory
       - 60-day (1 quarter): factor persistence

       Your getSignalsHistory query computes 20-day forward returns using LIMIT 1 OFFSET 19 — that is the correct approach for NSE (picks the 20th trading day after the signal date,
        skipping weekends and holidays automatically via the price_history table).

       Separation of signal quality from execution noise

       Large funds separate:

       1. Signal alpha — measured on hypothetical close-to-close returns assuming zero slippage
       2. Implementation shortfall — the gap between theoretical and realized P&L once trading costs, market impact, and fills are applied

       For a personal analyser, this separation is simpler: your signal journal captures (1) directly. The fact that you're not actually trading means (2) is zero. This is actually
       an advantage — you get a pure read on signal quality.

       ---
       3. Information Coefficient (IC) Analysis

       Definition

       IC is the Pearson or Spearman rank correlation between the signal score at time t and the forward return at time t+N, computed cross-sectionally across all stocks on each
       date, then averaged across dates.

       IC_t = corr(score_i_t, return_i_{t→t+N})   for all stocks i on date t
       mean_IC = average over all dates t

       The rank IC (Spearman) is more robust because it's outlier-resistant. AQR papers often report both. WorldQuant exclusively uses rank IC.

       What values are considered "good"

       From Grinold & Kahn and confirmed by practitioners:


       ┌─────────────┬─────────────────────────────────────────────────────────────────────────┐
       │  IC (mean)  │                             Interpretation                              │
       ├─────────────┼─────────────────────────────────────────────────────────────────────────┤
       │ < 0.02      │ Noise / not useful                                                      │
       ├─────────────┼─────────────────────────────────────────────────────────────────────────┤
       │ 0.02 – 0.05 │ Weak but potentially tradeable with diversification                     │
       ├─────────────┼─────────────────────────────────────────────────────────────────────────┤
       │ 0.05 – 0.10 │ Good — typical of a solid single factor                                 │
       ├─────────────┼─────────────────────────────────────────────────────────────────────────┤
       │ 0.10 – 0.15 │ Strong — top-decile systematic factor                                   │
       ├─────────────┼─────────────────────────────────────────────────────────────────────────┤
       │ > 0.15      │ Exceptional — either a regime-specific edge or look-ahead contamination │
       └─────────────┴─────────────────────────────────────────────────────────────────────────┘

       The fundamental law of active management (Grinold) states: IR ≈ IC × √(N_bets). With only 15 stocks, even an IC of 0.10 gives IR ≈ 0.10 × √15 ≈ 0.39, which is meaningful.

       ICIR (Information Coefficient Information Ratio)

       This is mean(IC) / std(IC). It measures signal consistency, not just average strength. An IC of 0.06 that is stable (ICIR = 2.0) is far more valuable than an IC of 0.15 that
       oscillates wildly (ICIR = 0.5). Funds look for ICIR > 0.5 as the minimum bar for going live.

       ---
       4. Signal Decay Curves

       What they are

       A signal decay curve plots IC vs. holding period N. You compute IC at N=1, N=5, N=10, N=20, N=40, N=60 days and plot the resulting curve.

       What they reveal

       - Fast decay (IC drops to ~0 by day 5): The signal is capturing microstructure or short-term momentum. It requires high-frequency rebalancing to harvest the edge. Many
       technical signals (RSI, MACD crossovers) fall here.
       - Slow decay (IC meaningful at N=60): The signal is capturing a fundamental or macro trend. It can be traded at low turnover with lower transaction cost drag.
       - Hump shape (IC peaks at N=10–20 then decays): The signal has a specific optimal holding period. This is common for medium-term momentum.
       - Non-monotonic / recovers at long horizons: Often indicates mean reversion layered on top of momentum (the signal predicts a short-term move that then partially reverses,
       then resumes).

       For your Sicilian engine, the mean reversion factor (which you are already planning to fix per the priorities doc) would create a non-monotonic decay curve — this is
       precisely why it was irrational to blend anti-trend and trend factors.

       The academic source

       Grinold & Kahn "Active Portfolio Management" (2nd ed., McGraw Hill, 2000), Chapter 6 is the standard reference. The AQR paper "Fact, Fiction and Momentum Investing" (Asness
       et al., 2014, Journal of Portfolio Management) shows empirical decay curves for momentum specifically.

       ---
       5. Paper Trading / Shadow Mode

       The standard fund process

       1. Shadow mode deployment: The signal runs in production, logging every recommendation, but no actual orders are submitted. This period is typically 3–12 months for a new
       signal at a systematic fund. The reason is seasonal: you need enough time to cover different market regimes (at least one volatility spike, one trending period, one sideways
       period).
       2. Minimum viable sample size: Statistical significance for IC requires roughly N ≥ 30 independent bets. With 15 stocks scored daily, one month gives you ~15×22 = 330
       observations, but these are cross-sectionally correlated (all stocks move together), so the effective independent sample is closer to 22 dates. Three months gets you ~66
       independent IC observations — enough for a preliminary t-test.
       3. Go-live criteria used at funds (from Man Group and AQR public talks):
         - Mean IC > 0.03 with t-stat > 2.0 (i.e., mean_IC / (std_IC / √N) > 2)
         - ICIR > 0.5 over the shadow period
         - Hit rate > 52% over the shadow period (for binary LONG/SHORT signals)
         - No evidence of IC decay — the signal is still working at the end of the shadow period, not just at the beginning
       4. Regime-conditional validation: Funds separately compute IC during bull, bear, and high-volatility regimes. A signal that only works in calm markets (VIX < 15) has limited
       utility. Your VIX regime feature directly supports this — you can segment your IC analysis by vix_regime tertile.

       ---
       6. Relevant Papers and Public Sources

       Academic foundations:
       - Grinold & Kahn, "Active Portfolio Management" (2000) — the canonical text. IC analysis is Chapter 6. The Fundamental Law is Chapter 5.
       - Jegadeesh & Titman (1993), "Returns to Buying Winners and Selling Losers" — the original momentum paper, with implicit forward IC analysis across N=3,6,12 month horizons.

       AQR public papers (free on AQR.com):
       - "A Century of Evidence on Trend-Following Investing" (Hurst, Ooi, Pedersen, 2017) — contains signal IC analysis across multiple horizons and asset classes.
       - "Value and Momentum Everywhere" (Asness, Moskowitz, Pedersen, 2013) — cross-asset IC analysis.

       WorldQuant / Kakushadze:
       - Zura Kakushadze & Juan Andrés Serur, "101 Formulaic Alphas" (2016, SSRN 2701346) — describes exactly the signal journal / IC / decay analysis workflow used at WorldQuant.
       Freely available on SSRN.
       - Kakushadze, "Shrinkage Estimation of High-Dimensional Covariance Matrices" and related papers — discuss IC-based signal weighting.

       Man Group / MLQR:
       - "The Case for Combining Momentum and Value in Systematic Investing" (Man Institute) — discusses forward return analysis in live vs. backtest context.

       Practitioners' blogs (practical, openly shared):
       - Quantopian's now-archived tearsheet code (available on GitHub as pyfolio) implemented IC analysis including decay curves.
       - QuantLib / zipline documentation describes the factor_returns and mean_information_coefficient functions, which are the reference open-source implementations.
       - Marcos Lopez de Prado, "Advances in Financial Machine Learning" (Wiley, 2018) — Chapter 5 covers fractionally differentiated features and forward return labelling (his
       "triple-barrier method" is an alternative to your ±3% threshold). The book explains why standard CV is wrong for signals (directly relevant to your TimeSeriesSplit fix
       already documented in your memory).

       ---
       7. Practical Implementation — The Signal Journal

       Your codebase already has most of the infrastructure. Here is what exists and what is missing:

       What you already have

       - signals_log table with (signal_date, symbol, signal, composite_score) — logged automatically when /api/quant/scores is called (in api.js lines 534–543).
       - getSignalsHistory function that joins against price_history to compute 20-day forward returns — this is the core of a forward return analysis system.
       - /api/quant/signals/history endpoint to expose this data.

       What is missing for a complete signal journal

       A. Multi-horizon forward returns

       Your current query only computes forward_return_20d. You need N=1, N=5, N=10, N=20 columns at minimum to compute a decay curve. The SQL pattern is already in your
       getSignalsHistory — you just need to repeat the LEFT JOIN price_history exit_Nd pattern for each horizon.

       B. IC computation

       IC is not computed anywhere. It requires a Python or JS function that:

       # For each signal_date, compute cross-sectional correlation:
       ic_by_date = (
           df.groupby("signal_date")
           .apply(lambda g: g["composite_score"].corr(g["forward_return_20d"], method="spearman"))
       )
       mean_ic = ic_by_date.mean()       # target: > 0.03
       icir    = mean_ic / ic_by_date.std()  # target: > 0.5

       The method="spearman" is the standard in production because it is rank-based and not distorted by outlier returns (circuit-limit days on NSE would distort Pearson).

       C. Hit rate

       For binary signals (LONG vs. SHORT), hit rate is simply the fraction of LONG signals followed by positive 20-day returns plus SHORT signals followed by negative 20-day
       returns:

       hit = (
           ((df["signal"] == "LONG")  & (df["forward_return_20d"] > 0)) |
           ((df["signal"] == "SHORT") & (df["forward_return_20d"] < 0))
       )
       hit_rate = hit.sum() / (df["signal"] != "HOLD").sum()
       # Target: > 0.52 to be statistically meaningful with ≥ 100 trades

       D. IC decay curve

       Requires computing IC at each horizon N ∈ {1, 5, 10, 20, 40, 60} and plotting them. This is a FastAPI endpoint (/api/quant/signal-quality) returning a list of {horizon_days,
       mean_ic, icir, n_observations} objects.

       E. Regime-conditional IC

       Segment the IC by VIX regime (already in your market_regime table). High-VIX IC tells you if the signal still works during panics — a critical validation for the NSE market.

       The minimal viable signal quality endpoint

       The simplest addition to your Python quant engine would be a new router endpoint at /api/signal-quality that:

       1. Reads from signals_log joined to price_history for N=1,5,10,20 day returns
       2. Computes rank IC per date per horizon
       3. Returns {horizon, mean_ic, icir, hit_rate, n_obs} for each horizon
       4. Optionally splits by VIX regime

       This endpoint would consume no RapidAPI calls (pure SQLite reads) and could run on the existing Python FastAPI engine without any new dependencies.

       ---
       Summary of Key Numbers to Target




       ┌───────────────────────┬──────────────────┬───────────┬─────────────┐
       │        Metric         │  Minimum viable  │   Good    │ Exceptional │
       ├───────────────────────┼──────────────────┼───────────┼─────────────┤
       │ Mean IC (rank, 20d)   │ 0.03             │ 0.06–0.10 │ > 0.12      │
       ├───────────────────────┼──────────────────┼───────────┼─────────────┤
       │ ICIR                  │ 0.5              │ 1.0–2.0   │ > 2.5       │
       ├───────────────────────┼──────────────────┼───────────┼─────────────┤
       │ Hit rate (LONG+SHORT) │ 52%              │ 55–58%    │ > 60%       │
       ├───────────────────────┼──────────────────┼───────────┼─────────────┤
       │ Shadow period         │ 3 months         │ 6 months  │ 12 months   │
       ├───────────────────────┼──────────────────┼───────────┼─────────────┤
       │ Min obs for t-stat    │ ~30 signal dates │ ~60 dates │ ~120 dates  │
       └───────────────────────┴──────────────────┴───────────┴─────────────┘

