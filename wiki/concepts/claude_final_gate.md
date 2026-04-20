# Claude Final Gate — LLM Execution Strategist

**Linear**: [SIC-31 — Angel One Buy/Sell Integration Automation](https://linear.app/sicilian/issue/SIC-31)
**Shipped**: 2026-04-21

## What It Is

A Claude (`claude-opus-4-7`) call that sits between the quant scoring engine and the broker. Every LONG signal that passes the hard conviction gates (composite ≥ 40, linear agreement, ML confidence, liquidity, data sufficiency) must also pass Claude's GO/NO_GO verdict before an order is placed.

Per SIC-31: *"Claude's output is the final say."*

```
Scoring engine → conviction gates (hard numerical) → Claude gate (GO/NO_GO) → user confirm → broker
    (Python)          (signalQueueService)              (claudeEvaluator)         (UI)      (Angel One)
```

## Why an LLM at This Layer

1. **Factor coherence** — Composite score ≥ 40 can hide a LONG where momentum is strong but relative strength is collapsing. A numeric gate can't flag the contradiction; an LLM reading the full factor vector can.
2. **Execution plan, not just yes/no** — Claude returns qty, limit price, stop, target, R:R in one shot. This is the bet-sizing + triple-barrier stop from López de Prado AFML expressed as a structured output.
3. **Human-in-the-loop affordance** — The user sees Claude's rationale before confirming. This is a cheap safety net while we're early in the live-trading curve and ML IC is ~0 on daily data (see `ml_pipeline.md`).
4. **Tunable policy** — Changes to risk methodology (e.g., "tighten stops in BEAR regime") become prompt edits, not code ships.

## The Prompt (Structural Overview)

Lives at `src/services/claudeEvaluator.js:42-112` — `SYSTEM_PROMPT` constant, cached via `cache_control: {type: 'ephemeral'}`. The 7-step analysis baked in:

| Step | Check | Source |
|------|-------|--------|
| 1 | Factor coherence (7 factors must tell a consistent story) | `factor_scoring.md` |
| 2 | Gap check: abort if &vert;current − signal&vert; > 3% | `riskLimits.execution.maxGapFromSignalPct` |
| 3 | Limit price (never market; anchor to current, avoid chase) | AHFT Ch.3, `cartea_jaimungal_ahft_2015.md` |
| 4 | Inverse-vol sizing, capped by position % + ADV + sector headroom | Kakushadze (R ~ V^0.76), Hurst (vol-scaled positions) |
| 5 | Volatility-scaled stop: `entry × (1 − 2.5 × σ_20d)` | López de Prado AFML Ch.3 triple-barrier |
| 6 | Target ≥ 1.5R (default 2R; extend to 3R on strong momentum + RS) | Asymmetry-is-the-edge principle |
| 7 | Final GO gate: all prior pass + qty ≥ 1 + sector headroom OK | Composed rule |

The user message (`buildUserMessage` at line 122) is a JSON blob with the live signal, factor snapshot, portfolio value, and sector exposure — volatile content placed after the cached prefix so the prompt cache hits.

## Output Schema

Claude must return a single JSON object — no prose, no markdown fences, enforced by explicit schema in the system prompt:

```json
{
  "decision": "GO" | "NO_GO",
  "symbol": "TATAPOWER",
  "qty": 15,
  "limit_price": 412.30,
  "stop_price": 389.50,
  "target_price": 458.00,
  "size_pct_of_portfolio": 2.3,
  "reward_to_risk": 2.1,
  "gap_pct": 0.42,
  "rationale": "3–6 sentences — why tradeable, how sized, how stop/target derived",
  "concerns": ["caveats even on GO, e.g. 'volume factor weak'"],
  "failed_checks": ["exact rule names if NO_GO"]
}
```

Parsed at `claudeEvaluator.js:183` with a single `JSON.parse()` — if Claude returns non-JSON the endpoint responds 502.

## API Surface

| Endpoint | Behavior |
|----------|----------|
| `POST /api/signal-queue/generate` | Pre-filters signals by conviction gates; skipped stocks returned in `skipped[]` with failed-gate reasons |
| `POST /api/signal-queue/:id/evaluate` | Calls Claude, returns `{plan, context, usage}`; does **not** mutate DB |
| `POST /api/signal-queue/:id/execute` | User confirmation — runs gap + risk checks → paper/live order |
| `POST /api/signal-queue/:id/reject` | User skip |

## UI Surface

`public/risk.html` — single-page flow:

1. Pending queue table (threshold-passers only)
2. Row click **Evaluate** → modal opens with spinner
3. Claude returns in ~5–15s → modal renders decision badge, 6-cell plan grid (Qty / Limit / Stop / Target / Size% / R:R), rationale block, concerns, factor chips for cross-checking
4. On GO → **Confirm & Execute** button hits `/execute`
5. On NO_GO → rationale + failed_checks displayed; only **Skip / Close**

## Risk Limits Enforced

All thresholds centralized in `src/config/riskLimits.js` and template-interpolated into the system prompt so the model sees the same numbers the gap check enforces:

| Limit | Value | Config key |
|-------|-------|-----------|
| Max single position | 5% of portfolio | `position.maxPositionPct` |
| Max sector concentration | 25% | `portfolio.maxSectorConcentrationPct` |
| Max volume participation | 3% of 20d ADV | `position.maxVolumeParticipationPct` |
| Max signal→current gap | 3% | `execution.maxGapFromSignalPct` |
| Target portfolio vol | 15% annualized | `position.targetVolAnnual` |
| Stop vol multiplier | 2.5 × σ_20d | `stopLoss.volMultiplier` |

## Conviction Gates (Pre-Claude Filter)

Also in `riskLimits.js → conviction`. Applied at queue generation so Claude never sees garbage:

- `minCompositeScore: 40` — project-canonical LONG threshold (`factor_scoring.md`)
- `requireLinearAgreement: true` — linear composite must also be LONG; ML IC is ~0 OOS on daily data (see `ml_pipeline.md` 2026-04-17 diagnostic)
- `minMlConfidencePct: 40` — mild "above random" filter for 3-class softmax; not a driver, just a confirmation
- `minAvgDailyVolume: 500_000` — liquidity gate (checklist M2)
- `minDataPoints: 200` — enough bars for 126d momentum, 26d MACD, 20d vol to be reliable

## Model & Cost

- Model: `claude-opus-4-7`
- Adaptive thinking: `thinking: {type: 'adaptive'}` — model decides budget per call
- Prompt cache: system block cached; cached hits reduce input cost ~90% after the first call each 5 minutes
- Max output tokens: 4096

## Gaps / Roadmap

- **No regime context yet** — the scoring engine exposes regime score but we don't pass it to Claude. Should be added once `/api/quant/regime` is stable. Would let Claude down-shift aggression in BEAR.
- **No fill telemetry** — Claude suggests a limit price but we don't compare to actual fill. Add post-execution write-back so a future version can learn drift.
- **No per-prompt A/B** — every call uses the same system prompt. Cold-testing prompt variations on paper mode would quantify the uplift.
- **Structured output via `output_config.format`** — we currently rely on the model respecting the schema declared in the system prompt. SDK 0.90.0 supports JSON-schema-enforced output; worth migrating once beta semantics are confirmed.
- **Angel One live path** — `executeSignal` returns `live_broker_not_implemented` in non-paper mode. That's the next chunk (SIC-31 continuation).

## Related Concepts / Papers

- [factor_scoring.md](factor_scoring.md) — What the 7 factors Claude reads actually measure
- [ml_pipeline.md](ml_pipeline.md) — Why ML confidence is a confirmation, not a driver
- [regime_detection.md](regime_detection.md) — Regime score (not yet wired into Claude)
- [lopez_de_prado_afml_2018.md](../papers/lopez_de_prado_afml_2018.md) — Ch.3 triple-barrier, Ch.17 bet sizing
- [kakushadze_101_alphas.md](../papers/kakushadze_101_alphas.md) — Inverse-vol sizing (R ~ V^0.76)
- [hurst_trend_following_century_2017.md](../papers/hurst_trend_following_century_2017.md) — Vol-scaled positions
- [grinold_kahn_active_portfolio.md](../papers/grinold_kahn_active_portfolio.md) — IC framework calibration

## Files

| Purpose | Path |
|---------|------|
| Prompt + SDK client | `src/services/claudeEvaluator.js` |
| Conviction gates + context builder | `src/services/signalQueueService.js` |
| API routes | `src/routes/api.js` (signal-queue block) |
| Risk thresholds | `src/config/riskLimits.js` |
| UI | `public/risk.html` |
