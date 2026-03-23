import { syncAllFundamentals } from './src/services/fundamentalsService.js';
process.env.USE_MOCK_FUNDAMENTALS = 'true';
console.log('Starting sync using Mock JSON...');
syncAllFundamentals().then(() => {
  console.log('✅ Sync Completed');
  process.exit(0);
}).catch(e => {
  console.error(e);
  process.exit(1);
});
