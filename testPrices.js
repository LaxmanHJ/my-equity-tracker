import { getAllQuotes, getPortfolioSummary } from './src/services/stockData.js';

async function main() {
  const summary = await getPortfolioSummary();
  console.log("Total Invested:", summary.summary.totalInvested);
  console.log("Total Current:", summary.summary.currentValue);
  console.log("Total P&L:", summary.summary.totalProfitLoss);
  
  for (let s of summary.holdings) {
    console.log(`${s.displaySymbol} (${s.symbol}): Qty ${s.quantity} | Avg ${s.avgPrice} | LTP ${s.price} | Invested ${s.invested} | Current ${s.currentValue} | P&L ${s.profitLoss}`);
  }
}
main();
