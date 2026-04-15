/**
 * Risk Manager Smoke Test
 *
 * Runs the full on-demand risk-check pipeline against the live Turso
 * portfolio and prints a summary. Throws on any unexpected error.
 *
 *   node scripts/smoke-risk.js
 */
import 'dotenv/config';
import { portfolio } from '../src/config/portfolio.js';
import { runRiskChecks } from '../src/risk/riskManager.js';
import { createEodPriceProvider } from '../src/risk/priceProvider.js';
import { computePositionSizes } from '../src/risk/positionSizing.js';
import { riskLimits } from '../src/config/riskLimits.js';

function section(title) {
  console.log('\n' + '═'.repeat(70));
  console.log(' ' + title);
  console.log('═'.repeat(70));
}

(async () => {
  section('RISK MANAGER SMOKE TEST');
  console.log('Portfolio symbols:', portfolio.length);
  console.log('Paper trading:', riskLimits.paperTrading);

  const priceProvider = createEodPriceProvider(60);

  section('PRICE PROVIDER (EOD)');
  for (const p of portfolio.slice(0, 3)) {
    const { currentPrice, prevClose, bars } = await priceProvider(p.displaySymbol);
    console.log(`  ${p.displaySymbol.padEnd(12)} price=${currentPrice}  prev=${prevClose}  bars=${bars.length}`);
  }

  section('RUN RISK CHECKS');
  const result = await runRiskChecks(portfolio, priceProvider);
  console.log('  checkedAt:', result.checkedAt);
  console.log('  tradingHalted:', result.tradingHalted);
  console.log('  positionsChecked:', result.positionsChecked);
  console.log('  alertCount:', result.alertCount);
  console.log('  errors:', result.errors);

  section('CIRCUIT BREAKER');
  console.log(result.circuitBreaker);

  section('SECTOR EXPOSURES');
  for (const e of result.sector.exposures) {
    const bar = '█'.repeat(Math.round(e.valuePct / 2));
    console.log(`  ${e.sector.padEnd(22)} ${String(e.valuePct).padStart(6)}%  ${bar}`);
  }
  if (result.sector.breaches.length > 0) {
    console.log('  BREACHES:', result.sector.breaches);
  }

  section('ALERTS');
  if (result.alerts.length === 0) {
    console.log('  (none)');
  } else {
    for (const a of result.alerts) {
      console.log(`  [${a.severity}] ${a.type}: ${a.message}`);
    }
  }

  section('POSITION SIZING PREVIEW (AUM = ₹10,00,000)');
  const positions = [];
  for (const p of portfolio) {
    const { currentPrice, bars } = await priceProvider(p.displaySymbol);
    if (currentPrice) positions.push({ symbol: p.displaySymbol, currentPrice, bars });
  }
  const sizes = computePositionSizes(positions, 1_000_000);
  for (const s of sizes.slice().sort((a, b) => b.cappedWeight - a.cappedWeight)) {
    console.log(
      `  ${s.symbol.padEnd(12)} vol=${String(s.annualVol).padStart(6)} ` +
      `raw=${String((s.rawWeight * 100).toFixed(2)).padStart(6)}%  ` +
      `capped=${String((s.cappedWeight * 100).toFixed(2)).padStart(6)}%  ` +
      `value=₹${String(s.targetValue).padStart(10)}  shares=${s.targetShares}`
    );
  }

  section('SMOKE TEST PASSED');
  process.exit(0);
})().catch(err => {
  console.error('\n✗ SMOKE TEST FAILED');
  console.error(err);
  process.exit(1);
});
