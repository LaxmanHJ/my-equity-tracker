import 'dotenv/config';
import { createClient } from '@libsql/client';

const db = createClient({
  url: process.env.TURSO_DATABASE_URL,
  authToken: process.env.TURSO_AUTH_TOKEN,
});

// @libsql/client rejects undefined — coerce every arg to null if undefined
const nn = (...args) => args.map(v => v ?? null);

/**
 * Initialize database tables
 */
export async function initDatabase() {
  // Price history table
  await db.execute(`
    CREATE TABLE IF NOT EXISTS price_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      date TEXT NOT NULL,
      open REAL,
      high REAL,
      low REAL,
      close REAL,
      volume INTEGER,
      adj_close REAL,
      created_at TEXT DEFAULT (datetime('now')),
      UNIQUE(symbol, date)
    )
  `);

  // Alerts table
  await db.execute(`
    CREATE TABLE IF NOT EXISTS alerts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      type TEXT NOT NULL,
      threshold REAL NOT NULL,
      direction TEXT NOT NULL,
      is_active INTEGER DEFAULT 1,
      triggered_at TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Daily reports table
  await db.execute(`
    CREATE TABLE IF NOT EXISTS daily_reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      date TEXT NOT NULL UNIQUE,
      report_data TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Fundamental data tables (normalized)
  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_fundamentals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL UNIQUE,
      company_name TEXT,
      industry TEXT,
      pe_ratio REAL,
      pb_ratio REAL,
      eps_diluted REAL,
      dividend_yield REAL,
      roe_5y_avg REAL,
      roe_ttm REAL,
      net_profit_margin_ttm REAL,
      net_profit_margin_5y_avg REAL,
      gross_margin_ttm REAL,
      operating_margin_ttm REAL,
      revenue_growth_5y REAL,
      eps_growth_5y REAL,
      eps_growth_3y REAL,
      revenue_growth_3y REAL,
      debt_to_equity REAL,
      current_ratio REAL,
      quick_ratio REAL,
      interest_coverage REAL,
      free_cash_flow REAL,
      market_cap REAL,
      year_high REAL,
      year_low REAL,
      beta REAL,
      book_value_per_share REAL,
      revenue_ttm REAL,
      price_to_sales REAL,
      price_to_cash_flow REAL,
      payout_ratio REAL,
      total_debt REAL,
      total_equity REAL
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_fundamentals_sync (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL UNIQUE,
      fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
      status TEXT DEFAULT 'success',
      error_message TEXT,
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_peer_comparison (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      peer_name TEXT,
      peer_pe REAL,
      peer_pb REAL,
      peer_market_cap REAL,
      peer_roe_ttm REAL,
      peer_npm_ttm REAL,
      peer_debt_to_equity REAL,
      peer_dividend_yield REAL,
      peer_price REAL,
      peer_change_pct REAL,
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_financials (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      fiscal_year TEXT,
      end_date TEXT,
      statement_type TEXT,
      revenue REAL,
      gross_profit REAL,
      operating_income REAL,
      net_income REAL,
      eps_diluted REAL,
      total_assets REAL,
      total_liabilities REAL,
      total_equity REAL,
      total_debt REAL,
      cash_from_operations REAL,
      capex REAL,
      free_cash_flow REAL,
      UNIQUE(symbol, fiscal_year, statement_type),
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_news (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      headline TEXT,
      news_date TEXT,
      url TEXT,
      source TEXT,
      thumbnail_url TEXT,
      UNIQUE(symbol, headline),
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_analyst_ratings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL UNIQUE,
      strong_buy INTEGER DEFAULT 0,
      buy INTEGER DEFAULT 0,
      hold INTEGER DEFAULT 0,
      sell INTEGER DEFAULT 0,
      strong_sell INTEGER DEFAULT 0,
      total_analysts INTEGER DEFAULT 0,
      mean_rating REAL,
      risk_category TEXT,
      risk_std_dev REAL,
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS stock_shareholding (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      category TEXT NOT NULL,
      holding_date TEXT NOT NULL,
      percentage REAL,
      UNIQUE(symbol, category, holding_date),
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  await db.execute(`
    CREATE TABLE IF NOT EXISTS delivery_data (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol        TEXT NOT NULL,
      date          TEXT NOT NULL,
      traded_qty    INTEGER,
      delivery_qty  INTEGER,
      delivery_pct  REAL,
      circuit_hit   INTEGER DEFAULT 0,
      UNIQUE(symbol, date)
    )
  `);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_delivery_symbol ON delivery_data(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_delivery_date ON delivery_data(date)`);

  // Create indexes — each statement separately
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_price_history_symbol ON price_history(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(date)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON stock_fundamentals(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_peers_symbol ON stock_peer_comparison(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_financials_symbol ON stock_financials(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_sync_symbol ON stock_fundamentals_sync(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_news_symbol ON stock_news(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_analyst_symbol ON stock_analyst_ratings(symbol)`);
  await db.execute(`CREATE INDEX IF NOT EXISTS idx_shareholding_symbol ON stock_shareholding(symbol)`);

  console.log('Database initialized');
}

// ═══════════════════════════════════════════════════════
// Price History CRUD
// ═══════════════════════════════════════════════════════

/**
 * Save historical price data
 */
export async function savePriceHistory(symbol, data) {
  for (const item of data) {
    await db.execute({
      sql: `INSERT OR REPLACE INTO price_history (symbol, date, open, high, low, close, volume, adj_close)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      args: nn(symbol, item.date, item.open, item.high, item.low, item.close, item.volume, item.adjClose)
    });
  }
}

/**
 * Get historical price data from database
 */
export async function getPriceHistory(symbol, days = 365) {
  const result = await db.execute({
    sql: `SELECT * FROM price_history WHERE symbol = ? ORDER BY date ASC`,
    args: [symbol]
  });
  return result.rows;
}

/**
 * Get the most recent date we have data for a symbol
 */
export async function getLatestPriceDate(symbol) {
  const result = await db.execute({
    sql: `SELECT date FROM price_history WHERE symbol = ? ORDER BY date DESC LIMIT 1`,
    args: [symbol]
  });
  const row = result.rows[0] ?? null;
  return row ? row.date : null;
}

// ═══════════════════════════════════════════════════════
// Alerts CRUD
// ═══════════════════════════════════════════════════════

/**
 * Create a price alert
 */
export async function createAlert(symbol, type, threshold, direction) {
  const result = await db.execute({
    sql: `INSERT INTO alerts (symbol, type, threshold, direction) VALUES (?, ?, ?, ?)`,
    args: [symbol, type, threshold, direction]
  });
  return result.lastInsertRowid;
}

/**
 * Get active alerts
 */
export async function getActiveAlerts() {
  const result = await db.execute({
    sql: `SELECT * FROM alerts WHERE is_active = 1`,
    args: []
  });
  return result.rows;
}

/**
 * Mark alert as triggered
 */
export async function triggerAlert(id) {
  await db.execute({
    sql: `UPDATE alerts SET is_active = 0, triggered_at = datetime('now') WHERE id = ?`,
    args: [id]
  });
}

// ═══════════════════════════════════════════════════════
// Daily Reports CRUD
// ═══════════════════════════════════════════════════════

/**
 * Save daily report
 */
export async function saveDailyReport(date, reportData) {
  await db.execute({
    sql: `INSERT OR REPLACE INTO daily_reports (date, report_data) VALUES (?, ?)`,
    args: [date, JSON.stringify(reportData)]
  });
}

/**
 * Get daily report
 */
export async function getDailyReport(date) {
  const result = await db.execute({
    sql: `SELECT * FROM daily_reports WHERE date = ?`,
    args: [date]
  });
  const row = result.rows[0] ?? null;
  if (row) {
    row.report_data = JSON.parse(row.report_data);
  }
  return row;
}

// ═══════════════════════════════════════════════════════
// Fundamentals CRUD
// ═══════════════════════════════════════════════════════

/**
 * Save or update fundamental data for a stock
 */
export async function saveFundamentals(symbol, data) {
  await db.execute({
    sql: `INSERT OR REPLACE INTO stock_fundamentals (
      symbol, company_name, industry,
      pe_ratio, pb_ratio, eps_diluted, dividend_yield,
      roe_5y_avg, roe_ttm, net_profit_margin_ttm, net_profit_margin_5y_avg,
      gross_margin_ttm, operating_margin_ttm,
      revenue_growth_5y, eps_growth_5y, eps_growth_3y, revenue_growth_3y,
      debt_to_equity, current_ratio, quick_ratio, interest_coverage,
      free_cash_flow, market_cap, year_high, year_low, beta,
      book_value_per_share, revenue_ttm, price_to_sales, price_to_cash_flow,
      payout_ratio, total_debt, total_equity
    ) VALUES (
      ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?
    )`,
    args: nn(
      symbol, data.company_name, data.industry,
      data.pe_ratio, data.pb_ratio, data.eps_diluted, data.dividend_yield,
      data.roe_5y_avg, data.roe_ttm, data.net_profit_margin_ttm, data.net_profit_margin_5y_avg,
      data.gross_margin_ttm, data.operating_margin_ttm,
      data.revenue_growth_5y, data.eps_growth_5y, data.eps_growth_3y, data.revenue_growth_3y,
      data.debt_to_equity, data.current_ratio, data.quick_ratio, data.interest_coverage,
      data.free_cash_flow, data.market_cap, data.year_high, data.year_low, data.beta,
      data.book_value_per_share, data.revenue_ttm, data.price_to_sales, data.price_to_cash_flow,
      data.payout_ratio, data.total_debt, data.total_equity
    )
  });
}

/**
 * Save sync metadata for a stock
 */
export async function saveFundamentalsSync(symbol, status = 'success', errorMessage = null) {
  await db.execute({
    sql: `INSERT OR REPLACE INTO stock_fundamentals_sync (symbol, fetched_at, status, error_message)
          VALUES (?, datetime('now'), ?, ?)`,
    args: [symbol, status, errorMessage]
  });
}

/**
 * Save peer comparison data (replaces all peers for a symbol)
 */
export async function savePeers(symbol, peers) {
  await db.execute({
    sql: `DELETE FROM stock_peer_comparison WHERE symbol = ?`,
    args: [symbol]
  });
  for (const p of peers) {
    await db.execute({
      sql: `INSERT INTO stock_peer_comparison (
              symbol, peer_name, peer_pe, peer_pb, peer_market_cap,
              peer_roe_ttm, peer_npm_ttm, peer_debt_to_equity, peer_dividend_yield,
              peer_price, peer_change_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      args: nn(
        symbol, p.peer_name, p.peer_pe, p.peer_pb, p.peer_market_cap,
        p.peer_roe_ttm, p.peer_npm_ttm, p.peer_debt_to_equity, p.peer_dividend_yield,
        p.peer_price, p.peer_change_pct
      )
    });
  }
}

/**
 * Save financial statements (replaces all for a symbol)
 */
export async function saveFinancials(symbol, financials) {
  for (const f of financials) {
    await db.execute({
      sql: `INSERT OR REPLACE INTO stock_financials (
              symbol, fiscal_year, end_date, statement_type,
              revenue, gross_profit, operating_income, net_income, eps_diluted,
              total_assets, total_liabilities, total_equity, total_debt,
              cash_from_operations, capex, free_cash_flow
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      args: nn(
        symbol, f.fiscal_year, f.end_date, f.statement_type,
        f.revenue, f.gross_profit, f.operating_income, f.net_income, f.eps_diluted,
        f.total_assets, f.total_liabilities, f.total_equity, f.total_debt,
        f.cash_from_operations, f.capex, f.free_cash_flow
      )
    });
  }
}

/**
 * Get fundamentals for a single stock (with sync info and peers)
 */
export async function getFundamentals(symbol) {
  const fundResult = await db.execute({
    sql: `SELECT f.*, s.fetched_at, s.status as sync_status
          FROM stock_fundamentals f
          LEFT JOIN stock_fundamentals_sync s ON f.symbol = s.symbol
          WHERE f.symbol = ?`,
    args: [symbol]
  });
  const fundamentals = fundResult.rows[0] ?? null;

  if (!fundamentals) return null;

  const peersResult = await db.execute({
    sql: `SELECT * FROM stock_peer_comparison WHERE symbol = ?`,
    args: [symbol]
  });
  fundamentals.peers = peersResult.rows;

  const financialsResult = await db.execute({
    sql: `SELECT * FROM stock_financials WHERE symbol = ? ORDER BY end_date DESC`,
    args: [symbol]
  });
  fundamentals.financials = financialsResult.rows;

  return fundamentals;
}

/**
 * Get fundamentals for all stocks
 */
export async function getAllFundamentals() {
  const result = await db.execute({
    sql: `SELECT f.*, s.fetched_at, s.status as sync_status
          FROM stock_fundamentals f
          LEFT JOIN stock_fundamentals_sync s ON f.symbol = s.symbol
          ORDER BY f.symbol`,
    args: []
  });
  return result.rows;
}

/**
 * Get the last sync date for fundamentals
 */
export async function getFundamentalsSyncDate() {
  const result = await db.execute({
    sql: `SELECT MIN(fetched_at) as oldest, MAX(fetched_at) as newest, COUNT(*) as count
          FROM stock_fundamentals_sync
          WHERE status = 'success'`,
    args: []
  });
  return result.rows[0] ?? null;
}

/**
 * Save news articles (replaces existing if headline matches)
 */
export async function saveNews(symbol, newsItems) {
  for (const item of newsItems) {
    await db.execute({
      sql: `INSERT OR REPLACE INTO stock_news (symbol, headline, news_date, url, source, thumbnail_url)
            VALUES (?, ?, ?, ?, ?, ?)`,
      args: nn(symbol, item.headline, item.news_date, item.url, item.source, item.thumbnail_url)
    });
  }
}

/**
 * Get news articles for a stock
 */
export async function getNews(symbol, limit = 10) {
  const result = await db.execute({
    sql: `SELECT * FROM stock_news WHERE symbol = ? ORDER BY id DESC LIMIT ?`,
    args: [symbol, limit]
  });
  return result.rows;
}

/**
 * Save analyst ratings
 */
export async function saveAnalystRatings(symbol, data) {
  await db.execute({
    sql: `INSERT OR REPLACE INTO stock_analyst_ratings (
            symbol, strong_buy, buy, hold, sell, strong_sell,
            total_analysts, mean_rating, risk_category, risk_std_dev
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    args: nn(
      symbol, data.strong_buy, data.buy, data.hold, data.sell, data.strong_sell,
      data.total_analysts, data.mean_rating, data.risk_category, data.risk_std_dev
    )
  });
}

/**
 * Get analyst ratings for a stock
 */
export async function getAnalystRatings(symbol) {
  const result = await db.execute({
    sql: `SELECT * FROM stock_analyst_ratings WHERE symbol = ?`,
    args: [symbol]
  });
  return result.rows[0] ?? null;
}

/**
 * Save shareholding data
 */
export async function saveShareholding(symbol, data) {
  for (const item of data) {
    await db.execute({
      sql: `INSERT OR REPLACE INTO stock_shareholding (symbol, category, holding_date, percentage)
            VALUES (?, ?, ?, ?)`,
      args: nn(symbol, item.category, item.holding_date, item.percentage)
    });
  }
}

/**
 * Get shareholding data for a stock
 */
export async function getShareholding(symbol) {
  const result = await db.execute({
    sql: `SELECT * FROM stock_shareholding WHERE symbol = ? ORDER BY holding_date ASC`,
    args: [symbol]
  });
  return result.rows;
}

/**
 * Get sector momentum scores
 */
export async function getSectorMomentum() {
  const result = await db.execute({
    sql: `WITH RankedPrices AS (
            SELECT symbol, close,
                   ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY date DESC) as rn
            FROM price_history
          ),
          PrevPrices AS (
            SELECT curr.symbol,
                   ((curr.close - prev.close) / prev.close * 100.0) as pct_change
            FROM RankedPrices curr
            JOIN RankedPrices prev ON curr.symbol = prev.symbol AND prev.rn = 2
            WHERE curr.rn = 1 AND prev.close > 0
          )
          SELECT
            f.industry,
            COUNT(f.symbol) as stock_count,
            AVG(p.pct_change) as momentum_score
          FROM stock_fundamentals f
          JOIN PrevPrices p ON f.symbol = p.symbol
          WHERE f.industry IS NOT NULL AND f.industry != ''
          GROUP BY f.industry
          ORDER BY momentum_score DESC`,
    args: []
  });
  return result.rows;
}

// Initialize on import
await initDatabase();
