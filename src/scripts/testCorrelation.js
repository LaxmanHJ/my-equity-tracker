import { buildCorrelationMatrix } from '../analysis/correlation.js';

async function test() {
    console.log("Starting Correlation Matrix Build...");
    const t0 = Date.now();
    try {
        const result = await buildCorrelationMatrix(90);
        console.log(`Finished in ${Date.now() - t0}ms`);
        console.log(`Generated matrix for ${result.symbols.length} symbols.`);
        console.log("Sample row correlations:", result.matrix[0].correlations);
    } catch (err) {
        console.error("Error:", err);
    }
}

test();
