# Sentiment

News / event sentiment as an alpha factor for the Sicilian engine and the ML model. Treated as an independent factor category — empirical evidence (see "Research basis" below) suggests sentiment is largely uncorrelated with the existing price-derived factors and earns weight in its own right, especially around scheduled events and on small/mid-cap names.

## Research basis

- **Sentiment alpha decays fast** — strongest predictive power is intraday → next-1-day, with effects attenuating sharply past 5 days ([MDPI 2025](https://www.mdpi.com/1911-8074/18/8/412)).
- **Non-trading-hours sentiment > trading-hours sentiment** for predicting next-day returns. Overnight news flow embeds tomorrow's gap.
- **Around events** (earnings, regulatory, M&A, mgmt change), sentiment is 3–5× more predictive than during quiet periods.
- **FinBERT fine-tuned on Indian English-language financial news** (Moneycontrol, ET, Mint, BusinessLine) outperforms vanilla FinBERT by ~15% F1 on Indian-equity tagged corpora.
- **Honest caveat**: on liquid large-caps, sentiment alone rarely beats market efficiency. The structural edge sits on small/mid-caps with thin analyst coverage and slow news diffusion — which is exactly where the production portfolio is concentrated (BAJAJHIND, TMCV, TMPV, REPCOHOME, TANLA, AWL).

## Architecture

```
quant_engine/sentiment/
├── __init__.py        — public exports
├── sources.py         — fetchers (newsapi today; moneycontrol/ET/NSE stubs)
├── scorer.py          — pluggable text→score (textblob_v1, finbert_v1)
├── aggregator.py      — per-(symbol, date) rollup + Turso upsert
├── features.py        — derived features (sent_24h, sent_5d, momentum, n_5d)
└── backfill.py        — CLI entry point
```

### Sources

| Source | Status | Notes |
|---|---|---|
| `stock_news` | **live, primary** | Reads from the Turso `stock_news` table that Node's `POST /api/portfolio/sync` populates from the RapidAPI Indian Stock Exchange `recentNews` field. Already stock-tagged at the source — no entity-linking false positives. No extra API spend. |
| `newsapi` | live, supplementary | NewsAPI `/v2/everything` per-stock query. Needs `NEWS_API_KEY`. Naive name match has false-positive risk ("Tata Power" can hit Tata Steel articles). Boosts recall when `stock_news` is sparse. |
| `moneycontrol` | stub | Company-news RSS — parser TBD. |
| `economic_times` | stub | Markets/stocks RSS — parser TBD. |
| `nse_disclosures` | stub | NSE corporate-announcements API — needs session cookie handling. |

Default chain (`DEFAULT_SOURCES`): `("stock_news", "newsapi")`. The aggregator dedupes by URL so an article appearing in both sources is counted once, with `stock_news` winning (first in the chain). Override via `SENTIMENT_SOURCES=stock_news` (env) or `--source` CLI flag.

### Scorers

| Scorer | Status | Range | Notes |
|---|---|---|---|
| `claude_v1` | **live, primary** | [-1, +1] (`sentiment_score`) | Calls `claude-haiku-4-5-20251001` via the `anthropic` SDK. ~$0.0003 per headline ≈ $0.50/month at portfolio scale. Requires `ANTHROPIC_API_KEY`. Abstains cleanly when key/SDK missing so the chain falls through. |
| `textblob_v1` | available, fallback | [-1, +1] (polarity) | Already in `requirements.txt`. Coarse; misclassifies finance jargon. Catches everything when Claude is unavailable. |
| `finbert_v1` | optional dep | [-1, +1] as `P(pos) − P(neg)` | Lazy-imports `transformers` (~1.5 GB install). Local inference alternative to Claude when you don't want API spend. Not in the default chain. |

Default chain (`DEFAULT_SCORER_CHAIN`): `("claude_v1", "textblob_v1")` — Claude when key is set, TextBlob otherwise. Override per-call via `prefer=(...)` or globally via `SENTIMENT_SCORER`. The aggregator stores `scorer_version` (including the Claude model ID) per row so re-running with a different scorer doesn't silently mix label-set semantics.

**Cost envelope** (Claude haiku, 60 articles/day, 30 days/month):
- ~1,800 API calls/month × ~$0.0003 each = **~$0.54/month**
- Negligible relative to live capital; cheaper than NewsAPI's paid tier.

### Storage — `sentiment_daily`

```sql
CREATE TABLE IF NOT EXISTS sentiment_daily (
    symbol         TEXT NOT NULL,
    date           TEXT NOT NULL,           -- YYYY-MM-DD (UTC)
    sent_score     REAL,                    -- mean ∈ [-1, +1]
    n_articles     INTEGER NOT NULL,
    sources        TEXT,                    -- comma-separated source mix
    scorer_version TEXT,                    -- e.g. "finbert_v1"
    updated_at     TEXT NOT NULL,           -- ISO UTC
    PRIMARY KEY (symbol, date)
)
```

`ensure_schema()` is invoked on the first upsert so the table appears the first time `backfill.py` runs; no separate migration step.

### Features

`SentimentFeatures` (in `features.py`) exposes four fields, all roughly in [-1, +1]:

| Field | Definition |
|---|---|
| `sent_24h` | Sentiment of the most recent calendar day with data. |
| `sent_5d` | 5-day rolling mean. Captures slower narrative shifts. |
| `sent_momentum` | `sent_24h − sent_5d`. Acceleration / divergence signal. |
| `sent_n_5d` | Article count over last 5d, log-scaled to [-1, +1]. Low coverage ≈ -1; ≥ 50 articles ≈ +1. Useful as an *attention* feature distinct from polarity. |

Missing days return NaN — downstream callers should treat sentiment as a **soft** feature (the existing ML `SimpleImputer` fills with the training median; the linear composite weights it at 0% today, see below).

## Productionisation phases

### Phase 1 — Data collection (2026-05-12, shipped)
- Module scaffolding live in `quant_engine/sentiment/`.
- `sentiment_daily` schema auto-created on first write.
- **Force-sync integration**: `POST /api/sync/sentiment` Python endpoint invoked by Node's `POST /api/portfolio/sync` flow alongside VIX / FII / PCR. Runs after `getAllQuotes` populates `stock_news`, so news → score happens in a single force-sync pass.
- Manual CLI: `python -m quant_engine.sentiment.backfill --portfolio --days 1` (still supported; useful for backfilling >1 day history).
- Scoring engine (`scoring/composite.py`) attaches `sentiment` to every score payload (observational only — no gating).
- Weight in linear composite: **0%**. Reason: no live IC measurement yet.

### Phase 2 — Per-symbol IC measurement (target +6 months of data)
- Add `quant_engine/sentiment/diagnostic.py` with walk-forward IC at 1d / 5d / 20d horizons, per stock and pooled.
- Persist `data/sentiment_diagnostic.json`.

### Phase 3 — Composite integration
- If pooled IC ≥ 0.02 at any horizon and not strongly negatively correlated with momentum, add as a Sicilian factor at 8–10% weight, taking from momentum (25 → 20) and RSI (20 → 15).
- Add to ML feature set as `sentiment_score`, `sentiment_momentum`, `sentiment_attention`. Same SimpleImputer fallback as other soft features.

### Phase 4 — Event-driven mode
- Tag articles via `event_classifier.py` (earnings/guidance/M&A/regulatory/other).
- Within ±2 trading days of a scheduled event, increase sentiment weight by 1.5× (regime override in `regime_adaptive_strategy.py`).
- Backfill earnings calendar from NSE corporate actions API.

## Project Usage

- **Force sync**: `POST /api/portfolio/sync` (Node) automatically calls `POST /api/sync/sentiment?days=1` (Python) after refreshing prices + `stock_news`. No extra step needed.
- **Manual API call**: `curl -X POST 'http://localhost:5001/api/sync/sentiment?days=30&symbols=INFY,TANLA'` — useful for backfilling history or scoring an ad-hoc subset.
- **Manual CLI**: `python -m quant_engine.sentiment.backfill --portfolio --days 30 --dry-run` (same code path as the route).
- **Scorer override**: `--scorer textblob_v1` (default chain prefers Claude when `ANTHROPIC_API_KEY` is set).
- **Programmatic**: `from quant_engine.sentiment import run_pipeline, build_sentiment_features`.
- **Live exposure**: every `/api/scores` payload now includes a `sentiment` dict (24h / 5d / momentum / n_5d / n_articles_24h / last_date) — empty `{...}` while `sentiment_daily` is unpopulated.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables `claude_v1` scorer. Already used by Node's claude_final_gate. |
| `CLAUDE_SENTIMENT_MODEL` | `claude-haiku-4-5-20251001` | Override scorer model. Haiku is cheapest; opus is overkill for headlines. |
| `NEWS_API_KEY` | unset | Enables NewsAPI source. Optional — pipeline runs from `stock_news` alone if unset. |
| `SENTIMENT_SOURCES` | `stock_news,newsapi` | Comma-separated override of default chain. |
| `SENTIMENT_SCORER` | unset | If set, forces a single scorer (e.g. `textblob_v1`); skips the chain. |
| `FINBERT_MODEL` | `yiyanghkust/finbert-tone` | Override FinBERT checkpoint if using the local-inference path. |

## Tests

`quant_engine/tests/test_sentiment.py` — 10 unit tests covering date bucketing (UTC, naive, timezone conversion), aggregation (empty, all-scorers-abstain, group-by-symbol-and-date with merged source mix), and feature math (`_log_scale_count` boundaries + monotonicity, `SentimentFeatures.empty()` shape).

## Open follow-ups
1. **Moneycontrol + ET parsers** — extends recall significantly. ET tags by company slug; Moneycontrol exposes per-stock RSS.
2. **Entity linking** — current naive query `"Tata Power" AND stock` matches "Tata Steel" articles. Add NER + ticker map to filter false positives.
3. **Event classifier** — earnings vs M&A vs regulatory.
4. **FinBERT fine-tune on Indian financial corpus** — the published edge is ~15% F1; worth replicating before Phase 3.
5. **Twitter/X cashtag fetcher** — high signal pre-open, brutal rate limits. Defer.
6. **Per-source authority weighting** — replace straight mean with credibility-weighted aggregation.

## Related concepts
- [factor_scoring.md](factor_scoring.md) — where sentiment will plug in once Phase 3 ships.
- [ml_pipeline.md](ml_pipeline.md) — sentiment will become an ML feature in Phase 3.
- [regime_detection.md](regime_detection.md) — Phase 4 event boosts hook off the regime layer.
