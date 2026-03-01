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
    quantity: 0, // Update with your holdings
    avgPrice: 0  // Update with your average purchase price
  },
  {
    symbol: 'APLAPOLLO.NS',
    name: 'APL Apollo Tubes',
    displaySymbol: 'APLLTD',
    exchange: 'NSE',
    sector: 'Industrial/Metals',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'AWL.NS',
    name: 'Adani Wilmar',
    displaySymbol: 'AWL',
    exchange: 'NSE',
    sector: 'FMCG/Agri',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'BAJAJHIND.NS',
    name: 'Bajaj Hindustan Sugar',
    displaySymbol: 'BAJAJHIND',
    exchange: 'NSE',
    sector: 'Sugar',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'BANDHANBNK.NS',
    name: 'Bandhan Bank',
    displaySymbol: 'BANDHANBNK',
    exchange: 'NSE',
    sector: 'Banking',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'ZOMATO.NS',
    name: 'Zomato (Eternal)',
    displaySymbol: 'ETERNAL',
    exchange: 'NSE',
    sector: 'Tech/Food Delivery',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'INFY.NS',
    name: 'Infosys',
    displaySymbol: 'INFY',
    exchange: 'NSE',
    sector: 'IT Services',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'JIOFIN.NS',
    name: 'Jio Financial Services',
    displaySymbol: 'JIOFIN',
    exchange: 'NSE',
    sector: 'Financial Services',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'REPCOHOME.NS',
    name: 'Repco Home Finance',
    displaySymbol: 'REPCOHOME',
    exchange: 'NSE',
    sector: 'Housing Finance',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'TANLA.NS',
    name: 'Tanla Platforms',
    displaySymbol: 'TANLA',
    exchange: 'NSE',
    sector: 'Telecom/CPaaS',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'TATAELXSI.NS',
    name: 'Tata Elxsi',
    displaySymbol: 'TATAELXSI',
    exchange: 'NSE',
    sector: 'IT/Design',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'TATAPOWER.NS',
    name: 'Tata Power',
    displaySymbol: 'TATAPOWER',
    exchange: 'NSE',
    sector: 'Power & Utilities',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'TATASTEEL.NS',
    name: 'Tata Steel',
    displaySymbol: 'TATASTEEL',
    exchange: 'NSE',
    sector: 'Steel/Metals',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'TIINDIA.NS',
    name: 'Tube Investments of India',
    displaySymbol: 'TMCV',
    exchange: 'NSE',
    sector: 'Auto Components',
    quantity: 0,
    avgPrice: 0
  },
  {
    symbol: 'TIINDIA.NS',
    name: 'Tube Investments of India',
    displaySymbol: 'TMPV',
    exchange: 'NSE',
    sector: 'Auto Components',
    quantity: 0,
    avgPrice: 0
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
