import axios from 'axios';
const SCRIP_URL = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json';
const res = await axios.get(SCRIP_URL, { timeout: 60000, responseType: 'json' });
const rows = res.data;
console.log('Total rows:', rows.length);

// distinct instrumenttypes to locate where indices live
const types = {};
for (const r of rows) {
    const t = r.instrumenttype || '(blank)';
    types[t] = (types[t] || 0) + 1;
}
console.log('\nInstrumenttypes:', types);

const hits = rows.filter(r => {
    const n = (r.name || '').toUpperCase();
    const s = (r.symbol || '').toUpperCase();
    return (n === 'NIFTY' || n === 'NIFTY 50' || n === 'NIFTY50' || s === 'NIFTY' || s === 'NIFTY 50' ||
        n === 'SENSEX' || s === 'SENSEX' || n === 'BANKNIFTY' || s === 'BANKNIFTY');
});
console.log('\nPossible index matches:', hits.length);
for (const r of hits.slice(0, 10)) console.log(JSON.stringify(r));

const amxidx = rows.filter(r => r.instrumenttype === 'AMXIDX');
console.log('\nAMXIDX (index) rows:', amxidx.length);
for (const r of amxidx.slice(0, 15)) console.log(JSON.stringify(r));
