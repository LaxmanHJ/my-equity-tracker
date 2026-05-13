"""
Backfill / refresh entry point for the sentiment_daily table.

Two callable surfaces:

  CLI         — `python -m quant_engine.sentiment.backfill --portfolio --days 1`
                Used for ad-hoc seeding and historical backfills.

  Function    — `run_pipeline(symbols=..., days_back=..., ...)`
                Used by the FastAPI `POST /api/sync/sentiment` endpoint
                which the Node force-sync flow invokes. Same code path
                as the CLI; returns a dict suitable for the HTTP response.

Flags:
  --portfolio          Use the production portfolio (default)
  --symbols SYMS       Comma-separated list of NSE symbols
  --days N             Lookback window (default 1)
  --source SRC[,SRC]   Override sources (env SENTIMENT_SOURCES wins otherwise)
  --scorer NAME        Force a specific scorer
  --dry-run            Score articles, print rollup, skip Turso write

Examples:
  python -m quant_engine.sentiment.backfill --portfolio --days 1
  python -m quant_engine.sentiment.backfill --symbols INFY --days 30 --dry-run

Symbol → company-name map lives in this file (no cross-language coupling
to portfolio.js). Unknown symbols fall through to using the symbol itself
as the NewsAPI query — Indian financial press uses tickers freely.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable, Optional

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


def run_pipeline(
    symbols: Optional[Iterable[str]] = None,
    days_back: int = 1,
    sources: Optional[Iterable[str]] = None,
    scorer: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    End-to-end fetch → score → aggregate → upsert.

    Used by both the CLI and the `/api/sync/sentiment` FastAPI route. Safe
    to call repeatedly (UPSERT on the sentiment_daily PRIMARY KEY makes
    re-runs idempotent within a day).

    Args:
        symbols:  iterable of NSE symbols. None → full SYMBOL_TO_COMPANY map.
        days_back: lookback window in calendar days.
        sources:  iterable of source names. None → DEFAULT_SOURCES (or env).
        scorer:   force one scorer; None → default chain.
        dry_run:  fetch + score + log, but skip Turso writes.

    Returns:
        Summary dict shaped for HTTP responses:
        {
            "symbols": int, "articles": int, "rows_written": int,
            "per_symbol": [{"symbol", "n_articles", "rows"} ...],
            "dry_run": bool,
        }
    """
    sym_list = (
        [s.strip().upper() for s in symbols if s and s.strip()]
        if symbols is not None
        else list(SYMBOL_TO_COMPANY.keys())
    )
    enabled_sources = list(sources) if sources else None
    prefer_scorer = (scorer,) if scorer else None

    if not dry_run:
        ensure_schema()

    total_articles = 0
    total_rows = 0
    per_symbol: list[dict] = []

    for sym in sym_list:
        company = _resolve_company(sym)
        articles = fetch_all_sources(
            sym, company,
            days_back=days_back,
            enabled=enabled_sources,
        )
        total_articles += len(articles)
        if not articles:
            per_symbol.append({"symbol": sym, "n_articles": 0, "rows": 0})
            continue

        rows = aggregate_articles(articles, prefer_scorer=prefer_scorer)
        if not rows:
            logger.info("No scorable articles for %s — every scorer abstained", sym)
            per_symbol.append({"symbol": sym, "n_articles": len(articles), "rows": 0})
            continue

        if dry_run:
            for r in rows:
                logger.info(
                    "[dry-run] %s %s  score=%s n=%d sources=%s scorer=%s",
                    r["symbol"], r["date"], r["sent_score"],
                    r["n_articles"], r["sources"], r["scorer_version"],
                )
            written = 0
        else:
            written = upsert_rows(rows)

        total_rows += written
        per_symbol.append({
            "symbol": sym, "n_articles": len(articles), "rows": len(rows),
        })

    summary = {
        "symbols":      len(sym_list),
        "articles":     total_articles,
        "rows_written": total_rows,
        "per_symbol":   per_symbol,
        "dry_run":      dry_run,
    }
    logger.info(
        "Sentiment pipeline done: %d symbols, %d articles, %d rows %s",
        len(sym_list), total_articles, total_rows,
        "(dry-run)" if dry_run else "upserted",
    )
    return summary


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

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else None
    )
    sources = (
        [s.strip() for s in args.source.split(",") if s.strip()]
        if args.source
        else None
    )

    run_pipeline(
        symbols=symbols,
        days_back=args.days,
        sources=sources,
        scorer=args.scorer,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
