# Stock Portfolio Analyzer

A sophisticated, real-time web application for analyzing a portfolio of Indian (NSE/BSE) stocks. It provides deep technical analysis, correlation tracking, and dynamic risk assessment using RapidAPI historical data and an intelligent local SQLite caching system.

## Features

### 📊 Interactive Dashboard
A unified view of your entire portfolio, featuring real-time prices, 52-week ranges, daily P&L, and dynamic chart allocations.
![Dashboard](docs/dashboard.png)

### 📈 Deep Technical Analysis
Select any stock in your portfolio to view automatically calculated trading signals and technical indicators (RSI, MACD, Simple Moving Averages). It also calculates risk metrics like Annualized Volatility, Beta vs. NIFTY 50, Sharpe Ratio, and 95% Value at Risk.
![Analysis](docs/analysis.png)

### 🔗 Correlation Matrix
Discover how your holdings relate to each other. The correlation grid automatically highlights stocks that move together (positive correlation) and ones that diverge (negative correlation), generating an overall diversification score for your portfolio.
![Correlation](docs/correlation.png)

### 🔔 Price Alerts System
Set custom trigger points for any stock. The application actively tracks local thresholds and triggers notifications when key levels are breached.
![Alerts](docs/alerts.png)

## Technology Stack

- **Frontend**: Vanilla JavaScript (ES6+), HTML5, CSS3, Chart.js
- **Backend**: Node.js, Express.js
- **Database**: SQLite (for lightning-fast historical quote caching)
- **Data Source**: RapidAPI (Indian Stock Exchange API2)

## Local Setup

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd PersonalStockAnalyser
   ```

2. **Install dependencies:**
   ```bash
   npm install
   ```

3. **Configure Environment Variables:**
   Rename `.env.example` to `.env` and insert your RapidAPI Key:
   ```env
   PORT=3000
   NODE_ENV=development
   RAPIDAPI_KEY="your_api_key_here"
   ```

4. **Start the Development Server:**
   ```bash
   npm run dev
   ```

5. **Access the application:**
   Navigate your browser to `http://localhost:3000`.

## Architecture Highlights
- **Rate-Limit Resilience:** Initially fetching from Yahoo Finance caused 429 IP bans. The architecture was overhauled to use RapidAPI.
- **Intelligent Caching System:** To eliminate redundant API calls for current prices, the backend intercepts requests, analyzes the locally cached `1y` SQLite historical data, and constructs a current quote instantly without external network latency.

## License
MIT
