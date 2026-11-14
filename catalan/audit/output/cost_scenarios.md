| scenario | calls | calls_reduction_%_vs_actual | est_cost_usd | note |
|---|---|---|---|---|
| A. actual (archived real calls) | 34,866 | 0.00 | 14.49 | includes the re-scoring loop |
| B. each announcement scored once | 15,656 | 55.10 | 6.50 | fixes the rebuild bug only |
| C. B minus duplicates | 15,323 | 56.10 | 6.36 | exact + near + cross-day re-filings |
| D. C minus label-only headlines | 7,711 | 77.90 | 3.34 | HEADLINE arm only: no company-specific input. NOT a PDF-arm rule |
| E. D restricted to PIT NIFTY500 | 2,240 | 93.60 | 0.97 | a study-scope decision, not a data-quality one |
| C'. B minus T2 procedural types | 9,206 | 73.60 | 3.87 | candidate only; transcripts and AGM special business are exceptions |