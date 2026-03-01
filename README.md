# Stock Portfolio Analyzer

Personal stock portfolio analyzer for Indian NSE/BSE stocks with technical analysis, correlation tracking, and real-time alerts.

## Features

- 📊 **Real-time Dashboard** - Track all 15 stocks with live prices
- 📈 **Technical Analysis** - RSI, MACD, Moving Averages, Bollinger Bands
- 🔗 **Correlation Matrix** - Understand portfolio diversification
- ⚠️ **Risk Metrics** - Beta, Volatility, Sharpe Ratio, VaR
- 🔔 **Price Alerts** - Get notified when prices hit targets
- 📧 **Daily Reports** - Automated portfolio summaries

## Quick Start

```bash
# Install dependencies
npm install

# Copy environment template
cp .env.example .env

# Start development server
npm run dev
```

Open http://localhost:3000 in your browser.

## Tech Stack

- **Backend**: Node.js, Express.js
- **Data**: Yahoo Finance API, SQLite
- **Frontend**: Vanilla JS, Chart.js
- **Analysis**: Custom technical & risk analysis modules

## Documentation

See the walkthrough for detailed documentation on API endpoints and features.

## License

MIT
