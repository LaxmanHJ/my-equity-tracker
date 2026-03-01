import express from 'express';
import cors from 'cors';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { settings } from './config/settings.js';
import apiRoutes from './routes/api.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

const app = express();

// Middleware
app.use(cors());
app.use(express.json());

// Serve static files from public directory
app.use(express.static(join(__dirname, '../public')));

// API routes
app.use('/api', apiRoutes);

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
});

export default app;
