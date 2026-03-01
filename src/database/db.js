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

  // Create indexes
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_price_history_symbol ON price_history(symbol);
    CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(date);
    CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active);
  `);

  console.log('✅ Database initialized');
}

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

// Initialize on import
initDatabase();

export default db;
