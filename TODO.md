# TODO - PersonalStockAnalyser

## High Priority

### 🔴 Replace Yahoo Finance API with Paid APIs
**Added:** 2026-01-12  
**Status:** Pending

Yahoo Finance API is unreliable. Need to switch to paid stock data APIs.

**Options to consider:**
- [Alpha Vantage](https://www.alphavantage.co/) - Free tier available, good for Indian stocks
- [Polygon.io](https://polygon.io/) - Real-time & historical data
- [Twelve Data](https://twelvedata.com/) - Supports NSE/BSE
- [MarketStack](https://marketstack.com/) - Simple REST API

**Files to update:**
- `src/services/stockData.js` - Main data fetching logic
- `src/config/settings.js` - API configuration
- `package.json` - Remove yahoo-finance2, add new SDK
