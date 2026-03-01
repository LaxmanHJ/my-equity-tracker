import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

/**
 * Fetch historical chart data using RapidAPI (Indian Stock Exchange API)
 * https://rapidapi.com/suneetk92/api/indian-stock-exchange-api2/
 * 
 * @param {string} symbol - The stock symbol (e.g., 'tcs')
 * @param {string} period - '1m', '3m', '6m', '1y', '5y', 'max'
 * @returns {Promise<Array>} - Array of historical objects
 */
export async function getRapidApiChartData(symbol, period = '1m') {
    const apiKey = process.env.RAPIDAPI_KEY;

    if (!apiKey || apiKey === 'your-rapidapi-key') {
        console.warn('[RapidAPI] Warning: RAPIDAPI_KEY is not set or invalid in .env');
    }

    try {
        const response = await axios.get('https://indian-stock-exchange-api2.p.rapidapi.com/historical_data', {
            params: {
                stock_name: symbol,
                period: period,
                filter: 'price' // Assuming we just want price OHLC data
            },
            headers: {
                'X-RapidAPI-Key': apiKey,
                'X-RapidAPI-Host': 'indian-stock-exchange-api2.p.rapidapi.com'
            }
        });

        const rawData = response.data;
        if (!rawData || !rawData.datasets) {
            throw new Error("Invalid response format from RapidAPI");
        }

        // Find the Price and Volume datasets
        const priceDataset = rawData.datasets.find(d => d.metric === 'Price');
        const volumeDataset = rawData.datasets.find(d => d.metric === 'Volume');

        if (!priceDataset) {
            throw new Error("Price dataset not found in RapidAPI response");
        }

        const chartData = [];

        // Loop through the price values: [date, price_close_equivalent]
        // Note: This specific API seems to only return a single price point (Close) per day, not full OHLC. 
        // We will map Open/High/Low to the close price to maintain the OHLCV structure without breaking the frontend.
        for (let i = 0; i < priceDataset.values.length; i++) {
            const date = priceDataset.values[i][0];
            const price = parseFloat(priceDataset.values[i][1]);

            // Find corresponding volume if available
            let volume = 0;
            if (volumeDataset && volumeDataset.values[i] && volumeDataset.values[i][0] === date) {
                volume = parseInt(volumeDataset.values[i][1], 10);
            }

            chartData.push({
                date: date,
                open: price,   // Fallback since API only gives one price
                high: price,   // Fallback since API only gives one price
                low: price,    // Fallback since API only gives one price
                close: price,
                volume: volume,
                source: 'rapidapi'
            });
        }

        return chartData;
    } catch (error) {
        console.error(`[RapidAPI] Error fetching chart data for ${symbol}:`, error.message);
        if (error.response) {
            console.error('[RapidAPI] Response Status:', error.response.status);
            console.error('[RapidAPI] Response Data:', JSON.stringify(error.response.data));
        }
        throw error;
    }
}

export default { getRapidApiChartData };
