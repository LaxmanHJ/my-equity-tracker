import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

/**
 * Alpha Vantage Service — Fallback for when RapidAPI fails.
 * Uses TIME_SERIES_DAILY endpoint with automatic key rotation across 6 free API keys.
 *
 * Symbol format: Indian stocks use SYMBOL.BSE (e.g., 'TCS.BSE', 'INFY.BSE')
 * Free tier: 25 requests/day per key, 1 request/second burst limit
 */

const ALPHA_VANTAGE_BASE_URL = 'https://www.alphavantage.co/query';

// Parse comma-separated keys from env
const API_KEYS = (process.env.ALPHAVANTAGE_KEYS || '')
    .split(',')
    .map(k => k.trim())
    .filter(k => k.length > 0);

// Track which key to use next (rotates on rate-limit)
let currentKeyIndex = 0;

/**
 * Delay helper for respecting the 1 req/sec rate limit
 */
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Convert a portfolio symbol to Alpha Vantage BSE format.
 * 'ADANIPOWER.NS' → 'ADANIPOWER.BSE'
 * 'ADANIPOWER'    → 'ADANIPOWER.BSE'
 * '^NSEI'         → null (indices not supported)
 */
function toAlphaVantageSymbol(symbol) {
    // Skip index/benchmark symbols
    if (symbol.startsWith('^')) {
        return null;
    }
    // Strip any existing exchange suffix and append .BSE
    const clean = symbol.replace(/\.(NS|BO|BSE)$/i, '');
    return `${clean}.BSE`;
}

/**
 * Fetch historical daily OHLCV data from Alpha Vantage with key rotation.
 *
 * @param {string} symbol - Stock symbol (e.g., 'INFY.NS', 'TATASTEEL', '^NSEI')
 * @param {string} outputsize - 'compact' (last 100 days) or 'full' (20+ years, premium only)
 * @returns {Promise<Array>} - Array of { date, open, high, low, close, volume, source } objects
 */
export async function getAlphaVantageChartData(symbol, outputsize = 'compact') {
    if (API_KEYS.length === 0) {
        throw new Error('No Alpha Vantage API keys configured. Set ALPHAVANTAGE_KEYS in .env');
    }

    const avSymbol = toAlphaVantageSymbol(symbol);
    if (!avSymbol) {
        console.warn(`[AlphaVantage] Skipping index symbol: ${symbol} (not supported)`);
        return [];
    }

    let lastError = null;

    // Try each key until one works
    for (let attempt = 0; attempt < API_KEYS.length; attempt++) {
        const keyIndex = (currentKeyIndex + attempt) % API_KEYS.length;
        const apiKey = API_KEYS[keyIndex];
        const maskedKey = apiKey.slice(0, 4) + '****';

        try {
            // Respect 1 req/sec rate limit
            if (attempt > 0) {
                await delay(1200);
            }

            const response = await axios.get(ALPHA_VANTAGE_BASE_URL, {
                params: {
                    function: 'TIME_SERIES_DAILY',
                    symbol: avSymbol,
                    outputsize: outputsize,
                    datatype: 'json',
                    apikey: apiKey
                },
                timeout: 15000
            });

            const data = response.data;

            // Check for rate-limit / info messages
            if (data['Information'] || data['Note']) {
                const msg = data['Information'] || data['Note'];
                console.warn(`[AlphaVantage] Key ${maskedKey} rate-limited: ${msg.substring(0, 80)}...`);
                lastError = new Error(`Key ${maskedKey} rate-limited`);
                continue; // Try next key
            }

            // Check for API errors
            if (data['Error Message']) {
                throw new Error(`API error for ${avSymbol}: ${data['Error Message']}`);
            }

            // Parse the time series data
            const timeSeries = data['Time Series (Daily)'];
            if (!timeSeries) {
                throw new Error(`No "Time Series (Daily)" in response for ${avSymbol}`);
            }

            // Convert to our standard format, sorted by date ascending
            const chartData = Object.entries(timeSeries)
                .map(([date, values]) => ({
                    date: date,
                    open: parseFloat(values['1. open']),
                    high: parseFloat(values['2. high']),
                    low: parseFloat(values['3. low']),
                    close: parseFloat(values['4. close']),
                    volume: parseInt(values['5. volume'], 10),
                    source: 'alphavantage'
                }))
                .sort((a, b) => new Date(a.date) - new Date(b.date));

            // Advance to this working key for next call
            currentKeyIndex = keyIndex;

            return chartData;

        } catch (error) {
            // If it's a rate-limit continue (already handled above), otherwise log and try next
            if (error.response && error.response.status === 429) {
                console.warn(`[AlphaVantage] Key ${maskedKey} got HTTP 429. Rotating...`);
                lastError = error;
                continue;
            }
            // For non-retryable errors, throw immediately
            if (!error.message.includes('rate-limited')) {
                throw error;
            }
            lastError = error;
        }
    }

    // All keys exhausted
    currentKeyIndex = (currentKeyIndex + 1) % API_KEYS.length; // Rotate for next time
    throw new Error(`All ${API_KEYS.length} Alpha Vantage keys exhausted. Last error: ${lastError?.message}`);
}

export default { getAlphaVantageChartData };
