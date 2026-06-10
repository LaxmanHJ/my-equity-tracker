"""
Block-deal counterparty event study (follow-up to bulk_deal_drift.py).

Block deals are negotiated crosses — every deal has a buyer row and a seller
row of equal value, so side-dominance over ALL participants is ≡ 0 and the
2026-06-10 bulk study excluded them by construction. The information, if any,
is in WHO is trading. This study classifies `client_name` into a declared
keyword taxonomy and asks: does net ACTIVE-institutional block flow (mutual
funds, insurers, pensions/SWFs, hedge funds) predict drift?

DECLARED TAXONOMY (fixed keyword lists, first match wins):
  PASSIVE — ETF/index plumbing (iShares + Mauritius feeders etc.). Internal
            transfers; expected NON-informative. Control cohort.
  DESK    — bank/ODI swap desks (Goldman ODI, SocGen, MS Asia…). Hedging
            flow; expected non-informative. Control cohort.
  ACTIVE  — MF / insurance / pension / SWF / hedge funds. The hypothesis
            cohort: discretionary conviction flow.
  OTHER   — everything else (promoters, individuals, unclassified corps).

DECLARED RULE (same machinery as bulk_deal_drift):
  Event = (symbol, date) where ACTIVE net block value dominance > 0.5
          (net/gross over ACTIVE rows only; gross over ACTIVE rows).
  Controls: same rule computed over DESK rows and PASSIVE rows.
  Entry close(t+1); market-adjusted drift at 1/5/10/20d; monthly-clustered t.
  Ship bar: ACTIVE_BUY |t| > 3 AND drift > 40bp at the horizon.

Usage:
    python3 -m quant_engine.research.block_deal_counterparty

Output: data/block_deal_counterparty.json + console summary.
"""
import argparse
import json
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quant_engine.data.loader import load_price_history, get_connection
from quant_engine.research.bulk_deal_drift import (
    _abnormal_returns, _summarize, HORIZONS, DOMINANCE_MIN,
)

logger = logging.getLogger(__name__)

OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "block_deal_counterparty.json"

TAXONOMY = [
    ("PASSIVE", ["ISHARES", "ETF", "INDEX", "VANGUARD", "POWERSHARES", "AMUNDI",
                 "WISDOMTREE", "INVESCO", "SPDR", "LYXOR", "XTRACKERS"]),
    ("DESK",    ["ODI", "SOCIETE GENERALE", "MORGAN STANLEY", "GOLDMAN SACHS",
                 "BNP PARIBAS", "CITIGROUP", "NOMURA", "JP MORGAN", "JPMORGAN",
                 "MERRILL", "UBS ", "BOFA", "BARCLAYS", "CREDIT SUISSE",
                 "DEUTSCHE BANK", "HSBC"]),
    ("ACTIVE",  ["MUTUAL FUND", "LIFE INSURANCE", "INSURANCE COMPANY",
                 "PENSION", "GOVERNMENT OF", "MONETARY AUTHORITY", "NORGES",
                 "ABU DHABI", "TRUSTEES", "MARSHALL WACE", "EUREKA",
                 "SEGANTII", "MILLENNIUM", "STEADVIEW", "WHITEOAK", "TIGER",
                 "CAPITAL GROUP", "FIDELITY", "T ROWE", "SCHRODER",
                 "ABERDEEN", "WELLINGTON", "GQG", "SMALLCAP WORLD",
                 "NEW WORLD FUND", "KOTAK FUNDS", "NIPPON INDIA",
                 "AXIS MAX LIFE", "HDFC LIFE", "SBI LIFE", "ICICI PRUDENTIAL LIFE"]),
]


def classify(name: str) -> str:
    up = f" {str(name).upper()} "
    for label, keywords in TAXONOMY:
        if any(k in up for k in keywords):
            return label
    return "OTHER"


def load_block_events() -> pd.DataFrame:
    conn = get_connection()
    try:
        deals = pd.read_sql_query(
            """
            SELECT date, symbol, client_name, trade_type, quantity, price
            FROM bulk_block_deals
            WHERE deal_type = 'BLOCK' AND date >= '2019-01-01'
              AND quantity > 0 AND price > 0
            """,
            conn,
        )
    finally:
        conn.close()

    deals["value"] = deals["quantity"] * deals["price"]
    deals["side"] = deals["trade_type"].str.upper().str.strip()
    deals = deals[deals["side"].isin(["BUY", "SELL"])]
    deals["who"] = deals["client_name"].map(classify)
    logger.info("Block rows by class: %s", deals["who"].value_counts().to_dict())

    events = []
    for who in ("ACTIVE", "DESK", "PASSIVE"):
        sub = deals[deals["who"] == who]
        g = sub.pivot_table(index=["symbol", "date"], columns="side",
                            values="value", aggfunc="sum", fill_value=0.0).reset_index()
        for side in ("BUY", "SELL"):
            if side not in g.columns:
                g[side] = 0.0
        gross = g["BUY"] + g["SELL"]
        g = g[gross > 0]
        g["dominance"] = (g["BUY"] - g["SELL"]) / (g["BUY"] + g["SELL"])
        g["event_side"] = np.where(g["dominance"] > DOMINANCE_MIN, "BUY",
                           np.where(g["dominance"] < -DOMINANCE_MIN, "SELL", None))
        g = g[g["event_side"].notna()].copy()
        g["deal_type"] = who   # rides through _abnormal_returns' cohort field
        g["gross_value_cr"] = (g["BUY"] + g["SELL"]) / 1e7
        events.append(g[["symbol", "date", "deal_type", "event_side",
                         "dominance", "gross_value_cr"]])
    return pd.concat(events, ignore_index=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    events = load_block_events()
    logger.info("Counterparty events: %d (%s)", len(events),
                events.groupby(["deal_type", "event_side"]).size().to_dict())

    nifty = load_price_history("^NSEI", limit=None)
    nifty_close = nifty["close"].astype(float)

    symbols = sorted(events["symbol"].unique())
    records = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_abnormal_returns, s, events[events["symbol"] == s], nifty_close): s
            for s in symbols
        }
        for fut in as_completed(futures):
            try:
                records.extend(fut.result())
            except Exception as exc:
                logger.warning("symbol %s failed: %s", futures[fut], exc)

    ar = pd.DataFrame(records)
    logger.info("Events with price coverage: %d / %d", len(ar), len(events))

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_classes": [t[0] for t in TAXONOMY] + ["OTHER"],
        "n_raw_events": int(len(events)),
        "n_covered_events": int(len(ar)),
        "cohorts": {},
        "ship_bar": "ACTIVE_BUY: |t_monthly| > 3 AND drift > 40bp",
    }
    print(f"\n{'cohort':16s} {'n':>6s}   " + "   ".join(f"{h}d: AR / t / hit" for h in HORIZONS))
    for (who, side), grp in ar.groupby(["deal_type", "side"]):
        name = f"{who}_{side}"
        s = _summarize(grp)
        results["cohorts"][name] = s
        if "1d" in s:
            cells = [f"{s[f'{h}d']['mean_ar_bp']:+7.1f}/{s[f'{h}d']['t_monthly']:+.1f}/{s[f'{h}d']['hit_pct']:.0f}%"
                     for h in HORIZONS]
            print(f"{name:16s} {s['n_events']:>6d}   " + "   ".join(cells))
        else:
            print(f"{name:16s} {s['n_events']:>6d}   insufficient (n<50)")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
