"""
Backfill / nightly-refresh CLI for the sentiment_daily table.

Modes:
  --portfolio          Use the Node portfolio's company list (default)
  --symbols SYMS       Comma-separated list of NSE symbols to process
  --days N             Lookback days (default 1 — meant for nightly cron)
  --source SRC[,SRC]   Comma-separated source list; default `newsapi`
                       (env SENTIMENT_SOURCES wins if set)
  --scorer NAME        Force a specific scorer (textblob_v1 / finbert_v1)
  --dry-run            Score articles, print rollup, skip Turso write

Examples:
  # nightly cron entry (1d window, NewsAPI)
  python -m quant_engine.sentiment.backfill --portfolio --days 1

  # one-off historical seed
  python -m quant_engine.sentiment.backfill --symbols INFY,TANLA --days 30

The company-name map lives in `quant_engine/sentiment/_companies.py` (not
the Node portfolio.js so this stays Python-pure). For symbols not in the
map the symbol itself is used as the query — typically still finds
results because Indian financial press uses tickers freely.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.sentiment.aggregator import aggregate_articles, upsert_rows, ensure_schema
from quant_engine.sentiment.sources import fetch_all_sources

logger = logging.getLogger(__name__)


# Best-effort symbol → company name map for the 15 production tickers
# (portfolio.js). Extend as the universe grows. When missing, the raw
# symbol is queried.
SYMBOL_TO_COMPANY = {
    "ADANIPOWER":  "Adani Power",
    "APLLTD":      "Alembic Pharmaceuticals",
    "AWL":         "Adani Wilmar",
    "BAJAJHIND":   "Bajaj Hindusthan Sugar",
    "BANDHANBNK":  "Bandhan Bank",
    "ETERNAL":     "Eternal",
    "INFY":        "Infosys",
    "JIOFIN":      "Jio Financial Services",
    "REPCOHOME":   "Repco Home Finance",
    "TANLA":       "Tanla Platforms",
    "TATAELXSI":   "Tata Elxsi",
    "TATAPOWER":   "Tata Power",
    "TATASTEEL":   "Tata Steel",
    "TMCV":        "TMCV",
    "TMPV":        "TMPV",
}


def _resolve_company(symbol: str) -> str:
    return SYMBOL_TO_COMPANY.get(symbol.upper(), symbol)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--portfolio", action="store_true",
                   help="Use the production portfolio (15 tickers)")
    g.add_argument("--symbols", type=str,
                   help="Comma-separated NSE symbols (e.g. INFY,TANLA)")
    p.add_argument("--days", type=int, default=1,
                   help="Lookback window in days (default 1)")
    p.add_argument("--source", type=str, default=None,
                   help="Comma-separated source list (overrides env)")
    p.add_argument("--scorer", type=str, default=None,
                   help="Force a specific scorer name; default = chain")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write to Turso — print rollup instead")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(SYMBOL_TO_COMPANY.keys())

    enabled_sources = None
    if args.source:
        enabled_sources = [s.strip() for s in args.source.split(",") if s.strip()]

    prefer_scorer = (args.scorer,) if args.scorer else None

    if not args.dry_run:
        ensure_schema()

    total_articles = 0
    total_rows = 0
    for sym in symbols:
        company = _resolve_company(sym)
        articles = fetch_all_sources(
            sym, company,
            days_back=args.days,
            enabled=enabled_sources,
        )
        total_articles += len(articles)
        if not articles:
            continue

        rows = aggregate_articles(articles, prefer_scorer=prefer_scorer)
        if not rows:
            logger.info("No scorable articles for %s — every scorer abstained", sym)
            continue

        if args.dry_run:
            for r in rows:
                logger.info(
                    "[dry-run] %s %s  score=%s n=%d sources=%s scorer=%s",
                    r["symbol"], r["date"], r["sent_score"],
                    r["n_articles"], r["sources"], r["scorer_version"],
                )
        else:
            total_rows += upsert_rows(rows)

    logger.info(
        "Done: %d symbols processed, %d articles fetched, %d rows %s",
        len(symbols), total_articles, total_rows,
        "(dry-run, nothing written)" if args.dry_run else "upserted",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
