/**
 * Portfolio Configuration
 * Your 15 stocks from NSE/BSE with sector classification
 */

export const portfolio = [
  {
    symbol: 'ADANIPOWER.NS',
    name: 'Adani Power',
    displaySymbol: 'ADANIPOWER',
    exchange: 'NSE',
    sector: 'Power & Utilities',
    quantity: 45,
    avgPrice: 65.83
  },
  {
    symbol: 'APLLTD.NS',
    name: 'Alembic Pharmaceuticals',
    displaySymbol: 'APLLTD',
    exchange: 'NSE',
    sector: 'Pharma',
    quantity: 30,
    avgPrice: 895.07
  },
  {
    symbol: 'AWL.NS',
    name: 'Adani Wilmar',
    displaySymbol: 'AWL',
    exchange: 'NSE',
    sector: 'FMCG/Agri',
    quantity: 5,
    avgPrice: 393.88
  },
  {
    symbol: 'BAJAJHIND.NS',
    name: 'Bajaj Hindustan Sugar',
    displaySymbol: 'BAJAJHIND',
    exchange: 'NSE',
    sector: 'Sugar',
    quantity: 500,
    avgPrice: 17.27
  },
  {
    symbol: 'BANDHANBNK.NS',
    name: 'Bandhan Bank',
    displaySymbol: 'BANDHANBNK',
    exchange: 'NSE',
    sector: 'Banking',
    quantity: 50,
    avgPrice: 246.29
  },
  {
    symbol: 'ETERNAL.NS',
    name: 'Eternal',
    displaySymbol: 'ETERNAL',
    exchange: 'NSE',
    sector: 'Other',
    quantity: 9,
    avgPrice: 129.37
  },
  {
    symbol: 'INFY.NS',
    name: 'Infosys',
    displaySymbol: 'INFY',
    exchange: 'NSE',
    sector: 'IT Services',
    quantity: 3,
    avgPrice: 1300.00
  },
  {
    symbol: 'JIOFIN.NS',
    name: 'Jio Financial Services',
    displaySymbol: 'JIOFIN',
    exchange: 'NSE',
    sector: 'Financial Services',
    quantity: 52,
    avgPrice: 252.60
  },
  {
    symbol: 'REPCOHOME.NS',
    name: 'Repco Home Finance',
    displaySymbol: 'REPCOHOME',
    exchange: 'NSE',
    sector: 'Housing Finance',
    quantity: 80,
    avgPrice: 471.18
  },
  {
    symbol: 'TANLA.NS',
    name: 'Tanla Platforms',
    displaySymbol: 'TANLA',
    exchange: 'NSE',
    sector: 'Telecom/CPaaS',
    quantity: 26,
    avgPrice: 948.20
  },
  {
    symbol: 'TATAELXSI.NS',
    name: 'Tata Elxsi',
    displaySymbol: 'TATAELXSI',
    exchange: 'NSE',
    sector: 'IT/Design',
    quantity: 14,
    avgPrice: 7196.65
  },
  {
    symbol: 'TATAPOWER.NS',
    name: 'Tata Power',
    displaySymbol: 'TATAPOWER',
    exchange: 'NSE',
    sector: 'Power & Utilities',
    quantity: 7,
    avgPrice: 218.79
  },
  {
    symbol: 'TATASTEEL.NS',
    name: 'Tata Steel',
    displaySymbol: 'TATASTEEL',
    exchange: 'NSE',
    sector: 'Steel/Metals',
    quantity: 200,
    avgPrice: 114.14
  },
  {
    symbol: 'TMCV.NS',
    name: 'TMCV',
    displaySymbol: 'TMCV',
    exchange: 'NSE',
    sector: 'Auto Components',
    quantity: 5,
    avgPrice: 102.07
  },
  {
    symbol: 'TMPV.NS',
    name: 'TMPV',
    displaySymbol: 'TMPV',
    exchange: 'NSE',
    sector: 'Auto Components',
    quantity: 5,
    avgPrice: 225.60
  }
];

// Get all unique symbols for API calls
export const getSymbols = () => [...new Set(portfolio.map(s => s.symbol))];

// Get stocks by sector
export const getStocksBySector = () => {
  const sectors = {};
  portfolio.forEach(stock => {
    if (!sectors[stock.sector]) sectors[stock.sector] = [];
    sectors[stock.sector].push(stock);
  });
  return sectors;
};

// Benchmark index for comparison
export const benchmark = {
  symbol: '^NSEI',
  name: 'NIFTY 50'
};

// Market indexes to analyse (Markov + Mean Reversion)
export const indexes = [
  { symbol: '^NSEI', name: 'NIFTY 50', rapidApiName: 'NIFTY 50' },
  { symbol: '^BSESN', name: 'SENSEX', rapidApiName: 'SENSEX' },
];
