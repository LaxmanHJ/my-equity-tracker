# Active Portfolio Management: A Quantitative Approach

**Authors**: Richard Grinold & Ronald Kahn  
**Published**: McGraw-Hill, 1999 (2nd edition)  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/Active Portfolio Management A Quantitative Approach... .pdf`  
**Status**: Placeholder (cover page only — large PDF, full ingestion pending)

---

## Core Framework

The definitive textbook on quantitative active management. Foundation for virtually all institutional factor investing.

## Key Concepts (from memory and references in other papers)

### The Fundamental Law of Active Management

**IR = IC × √BR**

Where:
- **IR** = Information Ratio (active return / active risk)
- **IC** = Information Coefficient (correlation between forecast and outcome)
- **BR** = Breadth (number of independent bets per year)

**Implication**: A modest IC (0.05–0.10) over many independent bets (high BR) generates a high IR. Diversification across many stocks/signals compounds small edges.

### Alpha vs. Risk Model

Strict separation:
1. **Alpha model**: Forecasts expected returns (our factor scores)
2. **Risk model**: Estimates covariance matrix (not yet implemented in this project)
3. **Portfolio construction**: Optimizes weights given alpha and risk model

### IC / ICIR Framework

- **IC** = correlation between predicted signal rank and actual next-period return rank
- **ICIR** = IC / std(IC) — measures consistency of the signal
- Good signals: IC ~0.05–0.10, ICIR > 0.5
- Referenced in Kakushadze (2015) and across quantitative literature

### Factor Risk Model

Decomposes return into:
- **Common factor returns** (market, sector, style)
- **Idiosyncratic return** (stock-specific)
- **Residual risk** (idiosyncratic variance)

Position sizing based on residual risk (not total risk) improves IR by isolating signal from factor noise.

### The Optimizer

Given alpha vector α and covariance matrix Σ:
- **Maximize**: α'w − λ·w'Σw (return − risk penalty)
- **Subject to**: constraints (long-only, sector limits, turnover)

λ is the risk aversion coefficient. For most active managers, λ ≈ 1/(2×IR_target).

---

## Project Usage

### Partially Implemented

- **IC tracking (2026-04-17)**: Pooled walk-forward IC is computed at `quant_engine/ml/diagnostic.py` and surfaced via `GET /api/quant/signal-quality/series`. Headline: **linear composite IC +0.040 at 20d, RF ML IC ≈ 0 on daily data**. See `wiki/concepts/ml_pipeline.md` for the full diagnostic context.
- **IC as Claude calibration (2026-04-21)**: The `claude_final_gate` system prompt encodes this IC result as a calibration note so Claude weights ML confidence as confirmation (not driver) and expects ~53% hit-rate — which is why it emphasizes asymmetric R:R ≥ 1.5 as the edge. See [claude_final_gate.md](../concepts/claude_final_gate.md).

### Where It Still Applies

- **Fundamental Law**: Our current 15-stock universe with 8 factors has limited breadth (BR ≈ 15). Expanding the universe increases BR.
- **Per-factor IC**: We track composite-level IC, not per-factor. Per-factor IC would let us re-weight the composite by factor quality (dynamic weights).
- **Risk model**: Factor risk model would improve position sizing beyond the inverse-vol sizer in the Claude gate.
- **Portfolio optimizer**: Required if we move from fixed-weight signals to optimal portfolio construction.

### Implementation Path

```python
# Roadmap:
# quant_engine/risk/ic_tracker.py — compute rolling IC for each factor
# quant_engine/risk/factor_risk_model.py — Barra-style sector/style decomposition
# quant_engine/portfolio/optimizer.py — mean-variance optimization with constraints
```

### Related Papers
- López de Prado Ch.16 (HRP) — avoids the optimizer entirely, more robust
- Ledoit & Wolf (2021) — shrinkage estimator for the covariance matrix
- Kakushadze (2015) — references IC/ICIR throughout
