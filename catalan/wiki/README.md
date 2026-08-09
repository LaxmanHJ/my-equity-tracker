# CATALAN wiki

CATALAN's own literature notes. Separate from the repo-root `wiki/` — this
project does not share conclusions with the Sicilian engine, the fund, or R2,
so it does not share a knowledge base with them either.

Same discipline as the root project:

- **Before implementing**: `grep -r "topic" catalan/wiki/` and read what's there.
- **After implementing**: update the `## Project Usage` section of the relevant page.
- **New paper**: create `catalan/wiki/papers/<slug>.md` — problem, method, key numbers, `## Project Usage`.
- **New concept**: create `catalan/wiki/concepts/<concept>.md`.
- Index every new page here.

Raw PDFs: `~/Desktop/Proyectos/Scilian-Books/Catalan/`

## Papers

| Page | Covers |
|---|---|
| [lopez_lira_tang_llm_return_predictability](papers/lopez_lira_tang_llm_return_predictability.md) | The source paper. GPT-4 headline scoring, drift, and the 20 bps death point. |

## Concepts

| Page | Covers |
|---|---|
| [llm_news_scoring](concepts/llm_news_scoring.md) | The technique and its India adaptations — session clock, cost model, contamination. |
