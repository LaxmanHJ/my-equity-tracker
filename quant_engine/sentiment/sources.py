"""
News sources for the sentiment pipeline.

Each fetcher returns a list of `Article` dicts:
    {
        "symbol":     str,        # NSE-style symbol (e.g. "INFY")
        "published":  datetime,   # UTC
        "title":      str,
        "body":       str,        # first paragraph or description; can be ""
        "source":     str,        # human label, e.g. "stock_news", "newsapi"
        "url":        str,        # canonical URL for dedup / debug
    }

Today active:
  * stock_news  — reads from the Turso `stock_news` table that the Node
                  force-sync flow populates from the RapidAPI Indian Stock
                  Exchange `recentNews` field. Already stock-tagged, no
                  entity-linking needed, no extra API spend. PRIMARY source.
  * newsapi     — NewsAPI `/v2/everything` per-stock query. Needs
                  NEWS_API_KEY; absent key → empty list. Naive name match
                  has false-positive risk ("Tata Power" can hit Tata Steel
                  articles); use as supplementary recall.

Stubs (parsers TBD — see wiki/concepts/sentiment.md): moneycontrol,
economic_times, nse_disclosures.

The aggregator deduplicates articles by URL before scoring, so re-fetching
the same window is idempotent.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env relative to project root so backfill CLIs work from any cwd
load_dotenv()


@dataclass
class Article:
    symbol: str
    published: datetime
    title: str
    body: str = ""
    source: str = ""
    url: str = ""

    @property
    def text(self) -> str:
        """Concatenate title + body for scoring. Headline carries most signal."""
        if self.body:
            return f"{self.title}. {self.body}"
        return self.title


# Headline pattern hints — broaden the NewsAPI query so we don't miss
# coverage when news outlets use "shares" / "stock price" instead of the
# company name. Kept short on purpose: NewsAPI's `everything` endpoint
# charges per request, not per match, and noisier queries help recall.
_NEWSAPI_KEYWORDS = '(stock OR shares OR NSE OR BSE OR earnings OR guidance OR results)'


def fetch_newsapi(
    symbol: str,
    company_name: str,
    days_back: int = 1,
    page_size: int = 50,
) -> list[Article]:
    """
    Fetch articles for one symbol via NewsAPI `/v2/everything`.

    Returns [] (not None) on missing key / failure so callers can iterate
    without guards. NEWS_API_KEY is read from env at call time so test
    harnesses can monkey-patch it.
    """
    api_key = os.getenv("NEWS_API_KEY")
    if not api_key:
        logger.info("NEWS_API_KEY missing — skipping NewsAPI fetch for %s", symbol)
        return []

    from_dt = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    params = {
        "q": f'"{company_name}" AND {_NEWSAPI_KEYWORDS}',
        "from": from_dt,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": min(page_size, 100),
        "apiKey": api_key,
    }
    try:
        resp = requests.get("https://newsapi.org/v2/everything", params=params, timeout=15)
    except requests.RequestException as exc:
        logger.warning("NewsAPI request failed for %s: %s", symbol, exc)
        return []

    if resp.status_code != 200:
        logger.warning(
            "NewsAPI returned %s for %s: %s", resp.status_code, symbol, resp.text[:200]
        )
        return []

    data = resp.json()
    out: list[Article] = []
    for a in data.get("articles", []) or []:
        published_raw = a.get("publishedAt")
        if not published_raw:
            continue
        try:
            pub = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append(Article(
            symbol=symbol,
            published=pub,
            title=a.get("title") or "",
            body=(a.get("description") or "")[:600],
            source="newsapi",
            url=a.get("url") or "",
        ))
    logger.info("NewsAPI: fetched %d articles for %s (last %dd)", len(out), symbol, days_back)
    return out


# ── stock_news (Turso) — primary source ──────────────────────────────────────
# The Node force-sync flow already populates this table from RapidAPI's
# Indian Stock Exchange `recentNews` field. Already stock-tagged, sourced
# from Indian financial press. No extra API spend.

def fetch_stock_news_from_db(symbol: str, days_back: int = 1) -> list[Article]:
    """
    Read articles for `symbol` from the Turso `stock_news` table.

    Schema (src/database/db.js):
      id, symbol, headline, news_date, url, source, thumbnail_url

    Date filter is "any row whose news_date is within `days_back` calendar
    days of today" — news_date arrives in mixed formats from RapidAPI
    (`YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DDTHH:MM:SSZ`, `DD MMM YYYY`), so we
    parse defensively. Rows that fail to parse are kept and treated as
    "now" so we don't lose signal on format quirks.

    Returns [] (not None) on missing table / DB failure.
    """
    from quant_engine.data.turso_client import connect

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Turso connection unavailable for stock_news (%s): %s", symbol, exc)
        return []

    try:
        cur = conn.execute(
            """
            SELECT headline, news_date, url, source
            FROM stock_news
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (symbol,),
        )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — likely missing table on first run
        logger.debug("stock_news read failed for %s: %s", symbol, exc)
        rows = []
    finally:
        conn.close()

    if not rows:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days_back))
    out: list[Article] = []
    for headline, news_date, url, src in rows:
        if not headline:
            continue
        pub = _parse_stock_news_date(news_date)
        if pub < cutoff:
            continue
        out.append(Article(
            symbol=symbol,
            published=pub,
            title=headline,
            body="",  # RapidAPI doesn't expose body; headline carries most signal
            source="stock_news" + (f":{src}" if src else ""),
            url=url or "",
        ))
    logger.info("stock_news: %d articles for %s (last %dd)", len(out), symbol, days_back)
    return out


def _parse_stock_news_date(raw) -> datetime:
    """
    Tolerant parser for the news_date column. Returns UTC-aware datetime.
    Unparseable inputs default to 'now' so the row stays in the recent window
    and doesn't get silently dropped — better to over-include than to lose.
    """
    if not raw:
        return datetime.now(timezone.utc)
    s = str(raw).strip()
    formats = (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y %H:%M",
        "%d %b %Y",
        "%d-%b-%Y",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    # ISO with offset
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    logger.debug("Unparseable news_date %r — defaulting to now", raw)
    return datetime.now(timezone.utc)


# ── Stubs for follow-up sources (see wiki/concepts/sentiment.md) ─────────────
# Implementations are deferred; calling them today returns [] so the
# aggregator can include them in its source list without breakage.

def fetch_moneycontrol(symbol: str, company_name: str, days_back: int = 1) -> list[Article]:
    """Moneycontrol company-news RSS. TODO: parse `/news/rss/<slug>.xml`."""
    logger.debug("moneycontrol fetch not yet implemented (symbol=%s)", symbol)
    return []


def fetch_economic_times(symbol: str, company_name: str, days_back: int = 1) -> list[Article]:
    """Economic Times Markets RSS. TODO: parse `/markets/stocks/news/<slug>/rssfeed`."""
    logger.debug("economic_times fetch not yet implemented (symbol=%s)", symbol)
    return []


def fetch_nse_disclosures(symbol: str, days_back: int = 1) -> list[Article]:
    """NSE corporate announcements REST API. TODO: handle session cookies."""
    logger.debug("nse_disclosures fetch not yet implemented (symbol=%s)", symbol)
    return []


# ── Dispatcher ───────────────────────────────────────────────────────────────

# Each entry: (callable, requires_company_name). stock_news + nse_disclosures
# only need the symbol (already tagged at source).
_SOURCE_FETCHERS = {
    "stock_news":      (fetch_stock_news_from_db, False),
    "newsapi":         (fetch_newsapi, True),
    "moneycontrol":    (fetch_moneycontrol, True),
    "economic_times":  (fetch_economic_times, True),
    "nse_disclosures": (fetch_nse_disclosures, False),
}

# Default chain: stock_news first (free, pre-tagged), NewsAPI for recall.
# Both run on every backfill; the aggregator dedupes by URL.
DEFAULT_SOURCES = ("stock_news", "newsapi")


def fetch_all_sources(
    symbol: str,
    company_name: str,
    days_back: int = 1,
    enabled: Optional[Iterable[str]] = None,
) -> list[Article]:
    """
    Run every enabled source and return a de-duplicated list of Articles.

    `enabled` defaults to the SENTIMENT_SOURCES env var (comma-separated)
    or DEFAULT_SOURCES. URLs are used for deduplication; titles fall back
    if URL is missing.
    """
    if enabled is None:
        env = os.getenv("SENTIMENT_SOURCES")
        enabled = (
            [s.strip() for s in env.split(",") if s.strip()]
            if env else list(DEFAULT_SOURCES)
        )

    seen: set[str] = set()
    out: list[Article] = []
    for name in enabled:
        spec = _SOURCE_FETCHERS.get(name)
        if spec is None:
            logger.warning("Unknown sentiment source: %s", name)
            continue
        fn, needs_name = spec
        try:
            articles = fn(symbol, company_name, days_back) if needs_name \
                else fn(symbol, days_back)
        except Exception as exc:  # noqa: BLE001 — third-party I/O
            logger.warning("source %s failed for %s: %s", name, symbol, exc)
            continue
        for a in articles:
            key = a.url or a.title
            if key and key not in seen:
                seen.add(key)
                out.append(a)
    return out
