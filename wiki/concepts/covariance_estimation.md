# Covariance Estimation

## The Problem

When p (number of assets) is comparable to T (number of observations), the sample covariance matrix is a poor estimator:
- Small eigenvalues are too small (diversification benefits overstated)
- Large eigenvalues are too large (risk is understated for concentrated positions)
- Matrix may be nearly singular → portfolio optimizer produces extreme, unstable weights

In our project: p=15 stocks, T=~252 (1 year of daily data) → p/T ≈ 0.06 (manageable, but still benefits from shrinkage)

## Current State

**NOT IMPLEMENTED.** The project uses equal-weight allocation — no covariance matrix needed.  
All 8 factor scores produce a LONG/HOLD/SHORT signal; position sizing is proportional or equal.

## Methods: Spectrum of Approaches

### 1. Sample Covariance (Baseline — Do Not Use)
```python
S = returns.cov()  # Biased when p/T > 0.1
```

### 2. Linear Shrinkage (sklearn)
```python
from sklearn.covariance import LedoitWolf
lw = LedoitWolf().fit(returns)
S_shrunk = lw.covariance_
```
Shrinks all eigenvalues toward a single target (identity or constant correlation). Available now; better than sample.

### 3. Nonlinear Shrinkage (Ledoit & Wolf 2021)
Each eigenvalue gets a different shrinkage amount based on its position in the spectrum.
- Captures ~100% of potential improvement
- Linear shrinkage fails when bulk eigenvalues are heterogeneous
- Implementation: `pyRMT` library or implement QuEST function directly
- See: [ledoit_wolf_shrinkage_2021.md](../papers/ledoit_wolf_shrinkage_2021.md)

### 4. Hierarchical Risk Parity (HRP — López de Prado Ch.16)
Avoids covariance inversion entirely:
1. Compute correlation matrix (more stable than covariance)
2. Hierarchical clustering → tree structure
3. Recursive bisection → allocate weights top-down by inverse variance

**Advantages**: No matrix inversion, stable with few observations, out-of-sample robust.  
**Best choice** for our 15-stock universe.

### 5. Factor Risk Model (Barra-style)
Decompose returns into:
- Factor returns (market, sector, style)
- Idiosyncratic returns

Covariance = B·F·B' + Δ (factor + idiosyncratic)

More data-efficient than full covariance; requires identifying factors.

## Implementation Roadmap

**Priority 1**: HRP (easiest, most robust, no matrix inversion)
```python
# quant_engine/risk/hrp.py
import scipy.cluster.hierarchy as sch
# 1. corr_matrix = returns.corr()
# 2. linkage = sch.linkage(dist_matrix, method='single')
# 3. recursive bisection for weights
```

**Priority 2**: Linear shrinkage (drop-in with sklearn for any MVO use case)  
**Priority 3**: Nonlinear shrinkage (full Ledoit & Wolf 2021 — maximum performance)

## When Is This Needed

- If adding **mean-variance portfolio optimization** (portfolio optimizer)
- If moving beyond equal-weight to **risk-parity** allocation
- If computing **portfolio VaR** (currently Node.js `analysis/` does this with simple beta)

## Related Papers
- [ledoit_wolf_shrinkage_2021.md](../papers/ledoit_wolf_shrinkage_2021.md) — nonlinear shrinkage theory
- [lopez_de_prado_afml_2018.md](../papers/lopez_de_prado_afml_2018.md) — HRP (Ch.16)
- [grinold_kahn_active_portfolio.md](../papers/grinold_kahn_active_portfolio.md) — factor risk model
