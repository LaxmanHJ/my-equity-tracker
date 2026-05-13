"""
Per-symbol daily aggregation of article sentiment into Turso.

Schema (created on first write):

    CREATE TABLE IF NOT EXISTS sentiment_daily (
        symbol         TEXT NOT NULL,
        date           TEXT NOT NULL,           -- YYYY-MM-DD (UTC)
        sent_score     REAL,                    -- volume-weighted mean ∈ [-1, +1]
        n_articles     INTEGER NOT NULL,
        sources        TEXT,                    -- comma-separated source mix
        scorer_version TEXT,                    -- e.g. "finbert_v1"
        updated_at     TEXT NOT NULL,           -- ISO UTC
        PRIMARY KEY (symbol, date)
    )

Aggregation rules:
  * All articles in a single aggregate_articles() call are scored via
    scorer.score_batch() — one batched API request to Claude (chunked
    internally) instead of one per article.
  * Articles with no score (every scorer abstained) are dropped silently
    and don't count toward n_articles. If every article in the day was
    dropped, no row is written (the caller can distinguish "no data" from
    "neutral data").
  * Daily score = mean of per-article scores. Volume weighting is a TODO
    until we have a credible per-article weight (impressions, source
    authority); a straight mean is honest until then.
  * Sources column records the *distinct* sources that contributed, not
    counts, to keep the column small.

Persistence is UPSERT (INSERT OR REPLACE) — re-running the same day
overwrites cleanly, which is what we want for nightly re-crawls.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from quant_engine.data.turso_client import TursoConnection, connect
from quant_engine.sentiment.scorer import ScorerInfo, score_batch
from quant_engine.sentiment.sources import Article

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sentiment_daily (
    symbol         TEXT NOT NULL,
    date           TEXT NOT NULL,
    sent_score     REAL,
    n_articles     INTEGER NOT NULL,
    sources        TEXT,
    scorer_version TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
)
"""

UPSERT_SQL = """
INSERT OR REPLACE INTO sentiment_daily
    (symbol, date, sent_score, n_articles, sources, scorer_version, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def ensure_schema(conn: Optional[TursoConnection] = None) -> None:
    """Create sentiment_daily if missing. Idempotent."""
    own = False
    if conn is None:
        conn = connect()
        own = True
    try:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        if own:
            conn.close()


def _date_key(dt: datetime) -> str:
    """YYYY-MM-DD in UTC. Single canonical bucket per article."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def aggregate_articles(
    articles: Iterable[Article],
    prefer_scorer: Optional[tuple[str, ...]] = None,
) -> list[dict]:
    """
    Score each article and roll up to (symbol, date) rows.

    All articles in the input are scored in a single batched call (Claude
    chunks internally) — for a 30-headline symbol this is 1 API request
    instead of 30. Returns a list of dicts shaped for UPSERT_SQL. Empty
    list if no article produced a usable score.
    """
    article_list = list(articles)
    if not article_list:
        return []

    results = score_batch([a.text for a in article_list], prefer=prefer_scorer)

    # bucket: (symbol, date) → list[(score, source)], plus the ScorerInfo seen
    buckets: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    scorer_seen: Optional[ScorerInfo] = None

    for a, (score, info) in zip(article_list, results):
        if score is None or info is None:
            continue
        key = (a.symbol, _date_key(a.published))
        buckets[key].append((score, a.source))
        # Last-seen wins. In practice all articles in a single run use the
        # same scorer (chain priority is deterministic), so this is stable.
        scorer_seen = info

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for (symbol, date), entries in buckets.items():
        scores = [s for s, _ in entries]
        srcs = sorted({src for _, src in entries if src})
        rows.append({
            "symbol":         symbol,
            "date":           date,
            "sent_score":     round(sum(scores) / len(scores), 4),
            "n_articles":     len(scores),
            "sources":        ",".join(srcs),
            "scorer_version": scorer_seen.version if scorer_seen else None,
            "updated_at":     now_iso,
        })
    return rows


def upsert_rows(rows: list[dict], conn: Optional[TursoConnection] = None) -> int:
    """
    Upsert aggregated rows. Returns count written. Creates the schema on
    first call so the caller doesn't need a separate migration step.
    """
    if not rows:
        return 0

    own = False
    if conn is None:
        conn = connect()
        own = True

    try:
        conn.execute(CREATE_TABLE_SQL)
        params_list = [
            (
                r["symbol"], r["date"], r["sent_score"], r["n_articles"],
                r["sources"], r["scorer_version"], r["updated_at"],
            )
            for r in rows
        ]
        conn.executemany(UPSERT_SQL, params_list)
        conn.commit()
        logger.info("Upserted %d sentiment_daily rows", len(rows))
        return len(rows)
    finally:
        if own:
            conn.close()
