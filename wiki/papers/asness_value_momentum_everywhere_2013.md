# Value and Momentum Everywhere

**Authors**: Clifford Asness, Tobias Moskowitz & Lasse Heje Pedersen  
**Published**: Journal of Finance, Vol. 68, No. 3, June 2013, pp. 929–985  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/ValueAndMomentumEverywhere.pdf`  
**Status**: Fully ingested

---

## The Finding

Value and momentum **both** work across 8 asset classes simultaneously. The −0.4 to −0.6 correlation between them is consistent across all classes, implying a common global factor structure.

## 8 Asset Classes Tested

1. US Equities
2. UK Equities
3. Continental European Equities
4. Japanese Equities
5. Fixed Income (government bonds, 10 countries)
6. Currencies (FX carry vs. value)
7. Equity index futures (country-level)
8. Commodity futures

## Factor Definitions

### Value
- **Equities**: Book-to-price (B/P), lagged 6 months to avoid look-ahead
- **Fixed income**: Real bond yield deviation from historical average
- **Currencies**: Real exchange rate deviation (PPP)
- **Commodities**: Spot price deviation from 5-year average

### Momentum
- **All assets**: Past 12-month return, skipping most recent month (12-1)
- Consistent definition across all 8 classes

## Key Results

| Portfolio | Sharpe Ratio |
|-----------|--------------|
| Value alone | ~0.53 |
| Momentum alone | ~0.65 |
| Value + Momentum (equal weight) | **~1.45** |

The combined SR ≈ 1.45 is **far greater** than either alone — diversification benefit is enormous due to −0.4 to −0.6 correlation.

## Value-Momentum Correlation

| Asset Class | Corr(Value, Momentum) |
|-------------|----------------------|
| US Equities | −0.57 |
| UK Equities | −0.42 |
| Other equity | −0.30 to −0.50 |
| Non-equity | −0.30 to −0.40 |

**Mechanism**: Value buys cheap assets (which have been falling — negative momentum). Momentum buys assets that have risen (which are now expensive — negative value). They are structurally anti-correlated.

## Global Factor Model

The paper tests a 3-factor global model:
1. Global value factor (across all 8 classes)
2. Global momentum factor (across all 8 classes)
3. Market factor (CAPM beta)

Findings:
- Within-asset-class value and momentum alphas disappear when regressed on the **global** factors
- Cross-asset-class co-movement: value assets in equities tend to co-move with value assets in currencies
- Suggests a **liquidity spiral** as common driver: forced selling by risk-averse/leveraged investors creates value opportunities and momentum reversals simultaneously

## Liquidity Risk Explanation

The correlation pattern is consistent with liquidity spirals (Brunnermeier & Pedersen):
- In crises, forced deleveraging creates undervalued assets (positive value opportunities) while breaking momentum
- Risk-tolerant investors who can provide liquidity earn the combined value+momentum premium
- **Prediction**: Both premia should be higher for less liquid assets → confirmed

## Project Usage

- **Strategic implication**: Adding a **value factor** to the current multi-factor score would be the single highest-impact improvement. The −0.4 correlation means it would reduce drawdowns significantly.
- **Possible value signals from available data**: P/E ratio, P/B ratio (from fundamental data if fetched), or price deviation from 52-week average (crude value proxy).
- **`quant_engine/factors/`**: No value factor exists yet. This paper motivates creating `quant_engine/factors/value.py`.
- **Cross-asset**: Not applicable (we trade only NSE equities), but the momentum definition (12-1) is already implemented correctly.
- **Roadmap**: Value factor + combine with existing momentum at ~50/50 for a SR approaching 1.4 (theoretical upper bound).
