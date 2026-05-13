"""
Unit tests for the sentiment pipeline.

Covers the pure aggregation + feature math. No external I/O — NewsAPI is
mocked, FinBERT is not exercised here (load is gated behind transformers
import), TextBlob does run if installed.

Run: python -m pytest quant_engine/tests/test_sentiment.py -v
"""
from __future__ import annotations

import math
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from quant_engine.sentiment.aggregator import aggregate_articles, _date_key
from quant_engine.sentiment.features import _log_scale_count, SentimentFeatures
from quant_engine.sentiment.scorer import _extract_score_from_claude_text, score_text
from quant_engine.sentiment.sources import (
    Article,
    _parse_stock_news_date,
    fetch_all_sources,
    DEFAULT_SOURCES,
)


def _mk(symbol, dt, title, body="", source="test", url="") -> Article:
    return Article(
        symbol=symbol, published=dt, title=title,
        body=body, source=source, url=url,
    )


class TestDateKey(unittest.TestCase):
    def test_utc_aware_datetime(self):
        dt = datetime(2026, 5, 12, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(_date_key(dt), "2026-05-12")

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2026, 5, 12, 18, 30)  # naive
        self.assertEqual(_date_key(dt), "2026-05-12")

    def test_non_utc_timezone_converted(self):
        # IST is UTC+5:30 — 02:00 IST on the 13th is 20:30 UTC on the 12th.
        ist = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2026, 5, 13, 2, 0, tzinfo=ist)
        self.assertEqual(_date_key(dt), "2026-05-12")


class TestAggregator(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(aggregate_articles([]), [])

    def test_drops_articles_when_all_scorers_abstain(self):
        # Force every scorer to abstain by patching score_batch. The aggregator
        # must produce zero rows rather than fabricating a neutral score.
        with patch(
            "quant_engine.sentiment.aggregator.score_batch",
            return_value=[(None, None)],
        ):
            rows = aggregate_articles([
                _mk("INFY", datetime(2026, 5, 12, tzinfo=timezone.utc),
                    "anything", source="test"),
            ])
        self.assertEqual(rows, [])

    def test_groups_by_symbol_and_date(self):
        # Two INFY articles on the same UTC day, one TANLA on a different day.
        # Expect 2 output rows (one per (symbol, date)), with INFY score = mean.
        # score_batch is now called once per aggregate_articles() invocation and
        # returns one (score, info) tuple per article in input order.
        with patch(
            "quant_engine.sentiment.aggregator.score_batch",
            return_value=[
                (0.6, _DummyInfo("textblob_v1")),
                (0.2, _DummyInfo("textblob_v1")),
                (-0.4, _DummyInfo("textblob_v1")),
            ],
        ):
            articles = [
                _mk("INFY", datetime(2026, 5, 12, 9, tzinfo=timezone.utc),
                    "INFY beats guidance", source="newsapi", url="u1"),
                _mk("INFY", datetime(2026, 5, 12, 15, tzinfo=timezone.utc),
                    "INFY hits highs", source="moneycontrol", url="u2"),
                _mk("TANLA", datetime(2026, 5, 13, 9, tzinfo=timezone.utc),
                    "TANLA in trouble", source="newsapi", url="u3"),
            ]
            rows = aggregate_articles(articles)

        by_key = {(r["symbol"], r["date"]): r for r in rows}
        self.assertEqual(len(rows), 2)

        infy = by_key[("INFY", "2026-05-12")]
        self.assertAlmostEqual(infy["sent_score"], 0.4, places=4)
        self.assertEqual(infy["n_articles"], 2)
        # sources column should be comma-separated, sorted, deduped
        self.assertEqual(infy["sources"], "moneycontrol,newsapi")
        self.assertEqual(infy["scorer_version"], "textblob_v1")

        tanla = by_key[("TANLA", "2026-05-13")]
        self.assertAlmostEqual(tanla["sent_score"], -0.4, places=4)
        self.assertEqual(tanla["n_articles"], 1)
        self.assertEqual(tanla["sources"], "newsapi")


class TestLogScaleCount(unittest.TestCase):
    def test_zero_articles_clamps_to_minus_one(self):
        self.assertEqual(_log_scale_count(0), -1.0)
        self.assertEqual(_log_scale_count(-5), -1.0)

    def test_soft_cap_calibration(self):
        # 50 articles should be ≈ +1 (matches the docstring contract).
        self.assertAlmostEqual(_log_scale_count(50, soft_cap=50.0), 1.0, places=5)

    def test_monotonic_increasing(self):
        prev = -math.inf
        for n in (0, 1, 5, 10, 25, 50, 100):
            v = _log_scale_count(n)
            self.assertGreaterEqual(v, prev)
            prev = v


class TestSentimentFeaturesEmpty(unittest.TestCase):
    def test_empty_dataclass_has_correct_shape(self):
        sf = SentimentFeatures.empty()
        self.assertIsNone(sf.sent_24h)
        self.assertIsNone(sf.sent_5d)
        self.assertIsNone(sf.sent_momentum)
        self.assertIsNone(sf.sent_n_5d)
        self.assertEqual(sf.n_articles_24h, 0)
        # to_dict must be JSON-serialisable (used by the API response).
        d = sf.to_dict()
        self.assertIn("sent_24h", d)
        self.assertIn("sent_momentum", d)


class _DummyInfo:
    """Stand-in for ScorerInfo so we can avoid importing it in fixtures."""
    def __init__(self, version):
        self.version = version


# ── Claude scorer parsing ────────────────────────────────────────────────────

class TestClaudeReplyExtraction(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(_extract_score_from_claude_text('{"score": 0.6}'), 0.6)

    def test_json_with_whitespace(self):
        self.assertEqual(_extract_score_from_claude_text('  {"score":-0.3}  '), -0.3)

    def test_extracts_from_fenced_reply(self):
        # Claude sometimes wraps JSON with prose despite the instruction.
        out = _extract_score_from_claude_text(
            "Here is the analysis: {\"score\": 0.42, \"reason\": \"upgrade\"}"
        )
        self.assertEqual(out, 0.42)

    def test_falls_back_to_first_number_in_range(self):
        # If JSON parsing fails entirely, regex picks up the first signed
        # decimal that's within [-1, 1].
        self.assertEqual(_extract_score_from_claude_text("sentiment score: 0.75"), 0.75)

    def test_rejects_out_of_range(self):
        self.assertIsNone(_extract_score_from_claude_text('{"score": 5.0}'))
        self.assertIsNone(_extract_score_from_claude_text('{"score": -2.0}'))

    def test_returns_none_for_garbage(self):
        self.assertIsNone(_extract_score_from_claude_text("no numbers here"))


class TestScoreTextChain(unittest.TestCase):
    """score_text delegates to score_batch which dispatches via _BATCH_SCORERS,
    so the chain semantics are exercised by patching the batch dict.
    """

    def test_chain_falls_through_when_first_abstains(self):
        # First scorer returns None — chain should try the next.
        with patch.dict(
            "quant_engine.sentiment.scorer._BATCH_SCORERS",
            {
                "claude_v1":   lambda texts: [None] * len(texts),
                "textblob_v1": lambda texts: [0.25] * len(texts),
            },
            clear=False,
        ):
            score, info = score_text(
                "INFY beats Q4 guidance",
                prefer=("claude_v1", "textblob_v1"),
            )
        self.assertEqual(score, 0.25)
        self.assertEqual(info.version, "textblob_v1")

    def test_chain_returns_none_when_all_abstain(self):
        with patch.dict(
            "quant_engine.sentiment.scorer._BATCH_SCORERS",
            {
                "claude_v1":   lambda texts: [None] * len(texts),
                "textblob_v1": lambda texts: [None] * len(texts),
            },
            clear=False,
        ):
            score, info = score_text("x", prefer=("claude_v1", "textblob_v1"))
        self.assertIsNone(score)
        self.assertIsNone(info)

    def test_clips_to_declared_range(self):
        # If a scorer returns out-of-range, score_text must clip to range.
        with patch.dict(
            "quant_engine.sentiment.scorer._BATCH_SCORERS",
            {"textblob_v1": lambda texts: [1.5] * len(texts)},
            clear=False,
        ):
            score, _ = score_text("x", prefer=("textblob_v1",))
        self.assertEqual(score, 1.0)


# ── stock_news source ────────────────────────────────────────────────────────

class TestStockNewsDateParser(unittest.TestCase):
    def test_iso_zulu(self):
        dt = _parse_stock_news_date("2026-05-12T14:30:00Z")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 5)
        self.assertEqual(dt.day, 12)
        self.assertIsNotNone(dt.tzinfo)

    def test_plain_date(self):
        dt = _parse_stock_news_date("2026-05-12")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 5, 12))

    def test_indian_press_format(self):
        # `12 May 2026` style sometimes appears in RapidAPI replies.
        dt = _parse_stock_news_date("12 May 2026")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 5, 12))

    def test_unknown_format_defaults_to_now(self):
        # Better to over-include than to silently drop the row.
        dt = _parse_stock_news_date("garbage format")
        # Should be within a minute of "now"
        self.assertLess(abs((datetime.now(timezone.utc) - dt).total_seconds()), 60)


class TestFetchAllSourcesDefaultChain(unittest.TestCase):
    def test_default_is_stock_news_then_newsapi(self):
        # Document the production default chain — guards against silent
        # config drift if someone reorders DEFAULT_SOURCES.
        self.assertEqual(DEFAULT_SOURCES, ("stock_news", "newsapi"))

    def test_dedup_by_url(self):
        # Two sources return overlapping URLs — only the first should survive.
        fake_stock_news = lambda _s, _d: [
            Article("INFY", datetime(2026, 5, 12, tzinfo=timezone.utc),
                    "INFY upgrade", source="stock_news", url="https://x/1"),
        ]
        fake_newsapi = lambda _s, _c, _d: [
            Article("INFY", datetime(2026, 5, 12, tzinfo=timezone.utc),
                    "INFY upgrade (dup)", source="newsapi", url="https://x/1"),
            Article("INFY", datetime(2026, 5, 12, tzinfo=timezone.utc),
                    "INFY new article", source="newsapi", url="https://x/2"),
        ]
        with patch.dict(
            "quant_engine.sentiment.sources._SOURCE_FETCHERS",
            {
                "stock_news": (fake_stock_news, False),
                "newsapi":    (fake_newsapi, True),
            },
            clear=False,
        ):
            out = fetch_all_sources("INFY", "Infosys", days_back=1,
                                    enabled=("stock_news", "newsapi"))
        urls = [a.url for a in out]
        # Two distinct URLs, with stock_news winning the duplicate.
        self.assertEqual(urls, ["https://x/1", "https://x/2"])
        self.assertEqual(out[0].title, "INFY upgrade")


if __name__ == "__main__":
    unittest.main()
