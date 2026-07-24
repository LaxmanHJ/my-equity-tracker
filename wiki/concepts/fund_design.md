# Fund Design — the multi-sleeve book (2026-07-25)

## Why it exists

Six months of research (2026-01 → 2026-06) established that per-stock
short-horizon prediction is **cost-bound** at retail Indian brokerage: the
strongest signal ever measured (rev3 short-horizon reversal, cs-IC +0.0195,
t=6.3 on 411k PIT rows) still produced negative net Sharpe at 35-78% daily
turnover vs 15-30bp one-way costs. Seven of eight declared PIT experiments
failed; regime timing has no alpha (Jensen α −1.2%/yr); no track cleared the
C0 ship gate. The legacy 15-stock book lost 19.9%, with a single 38.7%
position (TATAELXSI) accounting for essentially the entire loss.

The fund is the structural response: **beta bought cheaply, slow factors via
funds unless a pre-registered study earns self-management, one genuine risk
premium (VRP), and hedge-fund-grade risk process** — the transferable part of
institutional practice at retail scale.

**Design rule: no strategy with holding period under ~1 month enters the book.**

## Sleeves

| Sleeve | Budget | Implementation | Gate |
|---|---|---|---|
| BETA_CORE | 55% | Broad index ETF (default NIFTYBEES), 6 monthly tranches, level-independent | live |
| FACTOR_EQ | 25% | **Nifty200 Momentum 30 index fund** (Study A FAILED its self-management gate) | Study A: FAIL 2026-07-25 |
| VRP | 12% risk budget | SIC-92 defined-risk premium selling | 60-day collection gate, then capped net Sharpe > 0.5 |
| LS_FUT | 8% (pilot) | Market-neutral LS via stock futures | Study C: not run |

Event-driven (Study B) would overlay FACTOR_EQ only if a genuinely new
declared rule passes — PEAD and delivery-spike exact rules are dead.

## Architecture

- **Ledger is truth**: `fund_ledger` Turso table (BUY/SELL/DEPOSIT/WITHDRAW);
  positions, NAV, sleeve weights all derived on demand. Average-cost basis.
- **Surfaces**: `public/fund.html` + `public/js/fund.js` (own page, shared CSS,
  navbar link from Dashboard/Backtesting), `/api/fund/*` routes
  (`src/routes/fund.js`), logic in `src/services/fundService.js`.
- **Risk**: fund-specific limits in `src/config/riskLimits.js`
  (`fundRiskLimits`: 15% warn / 25% hard position cap — BETA_CORE index
  instruments exempt; −20% drawdown alert; sleeve budget +5% tolerance) plus
  the existing `src/risk/` stack (stop-loss, circuit breaker) reused via
  `runRiskChecks`. Breaches persist to `risk_alerts` and render on the page.
  Measurement and alerting only — **no broker orders originate anywhere**.
- **Data**: `syncOrchestrator.runEodSync()` (extracted from the Force Sync
  route) runs from three entry points: the dashboard button, an in-process
  node-cron (`ENABLE_LOCAL_CRON=1`), and the primary —
  `.github/workflows/eod-sync.yml` cloud cron (09:30 & 13:00 UTC weekdays),
  so collection continues laptop-off. It also gap-fills daily OHLC for any
  instrument in the fund ledger (ETFs are outside the legacy portfolio sync).
- **Staged entry**: tranche plan in `fund_config` (N equal monthly tranches,
  fixed day-of-month). Level-independent by design — the regime-timing study
  measured no timing skill, so "should I buy the dip?" is answered by
  schedule, not judgment.

## Discipline

Every sleeve unlock is a pre-registered study run once: declared rule in the
module docstring before the run, gates fixed, ship-or-kill, verdict wired
into the Fund page (`studyAGate()` pattern in fundService.js). The seven
honest failures of 2026-06 are the asset this process protects.

## Related
- [momentum.md](momentum.md) § Study A — the FACTOR_EQ gate (FAIL)
- [vol_risk_premium.md](vol_risk_premium.md) — the VRP sleeve's evidence base
- [regime_detection.md](regime_detection.md) — drawdown-control overlay (sizing only)
- [survivorship_pit_universe.md](survivorship_pit_universe.md) — why PIT is mandatory
