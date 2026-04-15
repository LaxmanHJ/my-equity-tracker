/**
 * One-off script to force-fetch price data for TATASTEEL, INFY, ADANIPOWER.
 */
import 'dotenv/config';
import { initDatabase, getLatestPriceDate } from '../src/database/db.js';
import { getHistoricalData } from '../src/services/stockData.js';

const SYMBOLS = ['TATASTEEL.NS', 'INFY.NS', 'ADANIPOWER.NS'];

async function main() {
  await initDatabase();

  for (const sym of SYMBOLS) {
    const clean = sym.replace('.NS', '');
    const before = await getLatestPriceDate(clean);
    console.log(`\n${clean}: latest before = ${before}`);

    await getHistoricalData(sym, '1m', true);  // force refresh

    const after = await getLatestPriceDate(clean);
    console.log(`${clean}: latest after  = ${after}`);
  }
}

main().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
