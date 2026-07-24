import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import cron from 'node-cron';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { settings } from './config/settings.js';
import apiRoutes from './routes/api.js';
import fundRoutes from './routes/fund.js';
import { runEodSync } from './services/syncOrchestrator.js';


const __dirname = dirname(fileURLToPath(import.meta.url));

const app = express();

// Middleware
app.use(cors());
app.use(express.json());

// Serve static files from public directory
app.use(express.static(join(__dirname, '../public')));

// API routes
app.use('/api', apiRoutes);
app.use('/api/fund', fundRoutes);

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Start server
app.listen(settings.port, () => {
  console.log(`
╔════════════════════════════════════════════════════════════╗
║                  📈 Stock Portfolio Analyzer               ║
╠════════════════════════════════════════════════════════════╣
║  Server running at: http://localhost:${settings.port}                  ║
║  Environment: ${settings.env.padEnd(43)}║
║  Dashboard: http://localhost:${settings.port}/                         ║
║  API Base: http://localhost:${settings.port}/api                       ║
╚════════════════════════════════════════════════════════════╝
  `);

  // Local self-sync fallback. The GitHub Actions workflow
  // (.github/workflows/eod-sync.yml) is the primary scheduler — it runs with
  // the laptop off. This in-process cron exists so a long-running local server
  // also self-heals (same idempotent pipeline; double-running is safe).
  // Opt-in via ENABLE_LOCAL_CRON=1 so `npm run dev` restarts don't spam APIs.
  if (process.env.ENABLE_LOCAL_CRON === '1') {
    const tz = settings.scheduler.timezone; // Asia/Kolkata
    // 15:00 IST (market hours — PCR/IV chain) and 18:30 IST (post-close EOD).
    for (const spec of ['0 15 * * 1-5', '30 18 * * 1-5']) {
      cron.schedule(spec, async () => {
        console.log(`[LocalCron] EOD sync fired (${spec} ${tz})`);
        try {
          const result = await runEodSync();
          const failed = Object.entries(result.subSyncs)
            .filter(([, s]) => !s.ok).map(([name]) => name);
          console.log(`[LocalCron] done — failed sub-syncs: ${failed.join(', ') || 'none'}`);
        } catch (e) {
          console.error('[LocalCron] sync failed:', e.message);
        }
      }, { timezone: tz });
    }
    console.log(`  ⏰ Local sync cron enabled (15:00 & 18:30 ${tz}, Mon-Fri)`);
  }
});

export default app;
