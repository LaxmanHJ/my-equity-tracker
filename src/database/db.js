import Database from 'better-sqlite3';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { existsSync, mkdirSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const dataDir = join(__dirname, '../../data');

// Ensure data directory exists
if (!existsSync(dataDir)) {
  mkdirSync(dataDir, { recursive: true });
}

const dbPath = join(dataDir, 'portfolio.db');
const db = new Database(dbPath);

// Enable WAL mode for better performance
db.pragma('journal_mode = WAL');

/**
 * Initialize database tables
 */
export function initDatabase() {
  // Price history table
  db.exec(`
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
  db.exec(`
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
  db.exec(`
    CREATE TABLE IF NOT EXISTS daily_reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      date TEXT NOT NULL UNIQUE,
      report_data TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // ── Fundamental data tables (normalized) ─────────────────────────
  db.exec(`
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

  db.exec(`
    CREATE TABLE IF NOT EXISTS stock_fundamentals_sync (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL UNIQUE,
      fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
      status TEXT DEFAULT 'success',
      error_message TEXT,
      FOREIGN KEY (symbol) REFERENCES stock_fundamentals(symbol)
    )
  `);

  db.exec(`
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

  db.exec(`
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

  // Create indexes
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_price_history_symbol ON price_history(symbol);
    CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(date);
    CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active);
    CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON stock_fundamentals(symbol);
    CREATE INDEX IF NOT EXISTS idx_peers_symbol ON stock_peer_comparison(symbol);
    CREATE INDEX IF NOT EXISTS idx_financials_symbol ON stock_financials(symbol);
    CREATE INDEX IF NOT EXISTS idx_sync_symbol ON stock_fundamentals_sync(symbol);
  `);

  console.log('✅ Database initialized');
}

// ═══════════════════════════════════════════════════════
// Price History CRUD
// ═══════════════════════════════════════════════════════

/**
 * Save historical price data
 */
export function savePriceHistory(symbol, data) {
  const insert = db.prepare(`
    INSERT OR REPLACE INTO price_history (symbol, date, open, high, low, close, volume, adj_close)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const insertMany = db.transaction((items) => {
    for (const item of items) {
      insert.run(symbol, item.date, item.open, item.high, item.low, item.close, item.volume, item.adjClose);
    }
  });

  insertMany(data);
}

/**
 * Get historical price data from database
 */
export function getPriceHistory(symbol, days = 365) {
  return db.prepare(`
    SELECT * FROM price_history 
    WHERE symbol = ? 
    ORDER BY date ASC 
  `).all(symbol);
}

/**
 * Get the most recent date we have data for a symbol
 */
export function getLatestPriceDate(symbol) {
  const row = db.prepare(`
    SELECT date FROM price_history 
    WHERE symbol = ? 
    ORDER BY date DESC 
    LIMIT 1
  `).get(symbol);

  return row ? row.date : null;
}

// ═══════════════════════════════════════════════════════
// Alerts CRUD
// ═══════════════════════════════════════════════════════

/**
 * Create a price alert
 */
export function createAlert(symbol, type, threshold, direction) {
  const result = db.prepare(`
    INSERT INTO alerts (symbol, type, threshold, direction)
    VALUES (?, ?, ?, ?)
  `).run(symbol, type, threshold, direction);

  return result.lastInsertRowid;
}

/**
 * Get active alerts
 */
export function getActiveAlerts() {
  return db.prepare(`
    SELECT * FROM alerts WHERE is_active = 1
  `).all();
}

/**
 * Mark alert as triggered
 */
export function triggerAlert(id) {
  db.prepare(`
    UPDATE alerts 
    SET is_active = 0, triggered_at = datetime('now')
    WHERE id = ?
  `).run(id);
}

// ═══════════════════════════════════════════════════════
// Daily Reports CRUD
// ═══════════════════════════════════════════════════════

/**
 * Save daily report
 */
export function saveDailyReport(date, reportData) {
  db.prepare(`
    INSERT OR REPLACE INTO daily_reports (date, report_data)
    VALUES (?, ?)
  `).run(date, JSON.stringify(reportData));
}

/**
 * Get daily report
 */
export function getDailyReport(date) {
  const row = db.prepare(`
    SELECT * FROM daily_reports WHERE date = ?
  `).get(date);

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
export function saveFundamentals(symbol, data) {
  db.prepare(`
    INSERT OR REPLACE INTO stock_fundamentals (
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
    )
  `).run(
    symbol, data.company_name, data.industry,
    data.pe_ratio, data.pb_ratio, data.eps_diluted, data.dividend_yield,
    data.roe_5y_avg, data.roe_ttm, data.net_profit_margin_ttm, data.net_profit_margin_5y_avg,
    data.gross_margin_ttm, data.operating_margin_ttm,
    data.revenue_growth_5y, data.eps_growth_5y, data.eps_growth_3y, data.revenue_growth_3y,
    data.debt_to_equity, data.current_ratio, data.quick_ratio, data.interest_coverage,
    data.free_cash_flow, data.market_cap, data.year_high, data.year_low, data.beta,
    data.book_value_per_share, data.revenue_ttm, data.price_to_sales, data.price_to_cash_flow,
    data.payout_ratio, data.total_debt, data.total_equity
  );
}

/**
 * Save sync metadata for a stock
 */
export function saveFundamentalsSync(symbol, status = 'success', errorMessage = null) {
  db.prepare(`
    INSERT OR REPLACE INTO stock_fundamentals_sync (symbol, fetched_at, status, error_message)
    VALUES (?, datetime('now'), ?, ?)
  `).run(symbol, status, errorMessage);
}

/**
 * Save peer comparison data (replaces all peers for a symbol)
 */
export function savePeers(symbol, peers) {
  db.prepare(`DELETE FROM stock_peer_comparison WHERE symbol = ?`).run(symbol);

  const insert = db.prepare(`
    INSERT INTO stock_peer_comparison (
      symbol, peer_name, peer_pe, peer_pb, peer_market_cap,
      peer_roe_ttm, peer_npm_ttm, peer_debt_to_equity, peer_dividend_yield,
      peer_price, peer_change_pct
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const insertMany = db.transaction((items) => {
    for (const p of items) {
      insert.run(
        symbol, p.peer_name, p.peer_pe, p.peer_pb, p.peer_market_cap,
        p.peer_roe_ttm, p.peer_npm_ttm, p.peer_debt_to_equity, p.peer_dividend_yield,
        p.peer_price, p.peer_change_pct
      );
    }
  });

  insertMany(peers);
}

/**
 * Save financial statements (replaces all for a symbol)
 */
export function saveFinancials(symbol, financials) {
  const insert = db.prepare(`
    INSERT OR REPLACE INTO stock_financials (
      symbol, fiscal_year, end_date, statement_type,
      revenue, gross_profit, operating_income, net_income, eps_diluted,
      total_assets, total_liabilities, total_equity, total_debt,
      cash_from_operations, capex, free_cash_flow
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const insertMany = db.transaction((items) => {
    for (const f of items) {
      insert.run(
        symbol, f.fiscal_year, f.end_date, f.statement_type,
        f.revenue, f.gross_profit, f.operating_income, f.net_income, f.eps_diluted,
        f.total_assets, f.total_liabilities, f.total_equity, f.total_debt,
        f.cash_from_operations, f.capex, f.free_cash_flow
      );
    }
  });

  insertMany(financials);
}

/**
 * Get fundamentals for a single stock (with sync info and peers)
 */
export function getFundamentals(symbol) {
  const fundamentals = db.prepare(`
    SELECT f.*, s.fetched_at, s.status as sync_status
    FROM stock_fundamentals f
    LEFT JOIN stock_fundamentals_sync s ON f.symbol = s.symbol
    WHERE f.symbol = ?
  `).get(symbol);

  if (!fundamentals) return null;

  fundamentals.peers = db.prepare(`
    SELECT * FROM stock_peer_comparison WHERE symbol = ?
  `).all(symbol);

  fundamentals.financials = db.prepare(`
    SELECT * FROM stock_financials WHERE symbol = ? ORDER BY end_date DESC
  `).all(symbol);

  return fundamentals;
}

/**
 * Get fundamentals for all stocks
 */
export function getAllFundamentals() {
  const rows = db.prepare(`
    SELECT f.*, s.fetched_at, s.status as sync_status
    FROM stock_fundamentals f
    LEFT JOIN stock_fundamentals_sync s ON f.symbol = s.symbol
    ORDER BY f.symbol
  `).all();

  return rows;
}

/**
 * Get the last sync date for fundamentals
 */
export function getFundamentalsSyncDate() {
  const row = db.prepare(`
    SELECT MIN(fetched_at) as oldest, MAX(fetched_at) as newest, COUNT(*) as count
    FROM stock_fundamentals_sync
    WHERE status = 'success'
  `).get();
  return row;
}

// Initialize on import
initDatabase();

export default db;
