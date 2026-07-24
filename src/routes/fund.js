/**
 * Fund API — the new multi-sleeve book. Mounted at /api/fund.
 *
 * All state lives in fund_* Turso tables; the legacy portfolio endpoints and
 * config/portfolio.js are untouched. The user executes trades manually at the
 * broker and records fills here — no order placement exists in this router.
 */
import express from 'express';
import {
  addFundLedgerEntry,
  getFundLedger,
  deleteFundLedgerEntry,
  setFundConfig,
} from '../database/db.js';
import { getFundOverview, ensureFundConfig, SLEEVES } from '../services/fundService.js';

const router = express.Router();

/** GET /api/fund/overview — NAV, positions, sleeves, risk, tranche, gates */
router.get('/overview', async (req, res) => {
  try {
    res.json(await getFundOverview());
  } catch (error) {
    console.error('[Fund] overview error:', error);
    res.status(500).json({ error: 'Failed to build fund overview' });
  }
});

/** GET /api/fund/ledger — full ledger (optionally ?sleeve=BETA_CORE) */
router.get('/ledger', async (req, res) => {
  try {
    const sleeve = req.query.sleeve || null;
    if (sleeve && !SLEEVES.includes(sleeve)) {
      return res.status(400).json({ error: `Unknown sleeve: ${sleeve}` });
    }
    res.json(await getFundLedger({ sleeve }));
  } catch (error) {
    console.error('[Fund] ledger error:', error);
    res.status(500).json({ error: 'Failed to read ledger' });
  }
});

/** POST /api/fund/ledger — record a fill or cash movement */
router.post('/ledger', async (req, res) => {
  try {
    const { sleeve, trade_date, instrument, side, qty, price, fees, note } = req.body || {};

    if (!SLEEVES.includes(sleeve)) {
      return res.status(400).json({ error: `sleeve must be one of ${SLEEVES.join(', ')}` });
    }
    if (!['BUY', 'SELL', 'DEPOSIT', 'WITHDRAW'].includes(side)) {
      return res.status(400).json({ error: 'side must be BUY, SELL, DEPOSIT or WITHDRAW' });
    }
    if (!trade_date || !/^\d{4}-\d{2}-\d{2}$/.test(trade_date)) {
      return res.status(400).json({ error: 'trade_date must be YYYY-MM-DD' });
    }
    if (!instrument || typeof instrument !== 'string') {
      return res.status(400).json({ error: 'instrument is required' });
    }
    const qtyNum = Number(qty);
    const priceNum = Number(price);
    if (!(qtyNum > 0) || !(priceNum >= 0)) {
      return res.status(400).json({ error: 'qty must be > 0 and price >= 0' });
    }

    const id = await addFundLedgerEntry({
      sleeve,
      trade_date,
      instrument: instrument.trim().toUpperCase(),
      side,
      qty: qtyNum,
      price: priceNum,
      fees: Number(fees) || 0,
      note: note || null,
    });
    res.json({ success: true, id });
  } catch (error) {
    console.error('[Fund] ledger insert error:', error);
    res.status(500).json({ error: 'Failed to record ledger entry' });
  }
});

/** DELETE /api/fund/ledger/:id — remove a mistaken entry */
router.delete('/ledger/:id', async (req, res) => {
  try {
    const deleted = await deleteFundLedgerEntry(Number(req.params.id));
    if (!deleted) return res.status(404).json({ error: 'No such ledger entry' });
    res.json({ success: true });
  } catch (error) {
    console.error('[Fund] ledger delete error:', error);
    res.status(500).json({ error: 'Failed to delete ledger entry' });
  }
});

/** GET /api/fund/config */
router.get('/config', async (req, res) => {
  try {
    res.json(await ensureFundConfig());
  } catch (error) {
    console.error('[Fund] config error:', error);
    res.status(500).json({ error: 'Failed to read config' });
  }
});

/** PUT /api/fund/config — update one key: { key, value } */
router.put('/config', async (req, res) => {
  try {
    const { key, value } = req.body || {};
    if (!key || value === undefined) {
      return res.status(400).json({ error: 'key and value are required' });
    }
    await setFundConfig(key, value);
    res.json({ success: true });
  } catch (error) {
    console.error('[Fund] config update error:', error);
    res.status(500).json({ error: 'Failed to update config' });
  }
});

export default router;
