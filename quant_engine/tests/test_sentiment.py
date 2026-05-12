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
from quant_engine.sentiment.sources import Article


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
        # Force every scorer to abstain by patching score_text. The aggregator
        # must produce zero rows rather than fabricating a neutral score.
        with patch(
            "quant_engine.sentiment.aggregator.score_text",
            return_value=(None, None),
        ):
            rows = aggregate_articles([
                _mk("INFY", datetime(2026, 5, 12, tzinfo=timezone.utc),
                    "anything", source="test"),
            ])
        self.assertEqual(rows, [])

    def test_groups_by_symbol_and_date(self):
        # Two INFY articles on the same UTC day, one TANLA on a different day.
        # Expect 2 output rows (one per (symbol, date)), with INFY score = mean.
        with patch(
            "quant_engine.sentiment.aggregator.score_text",
            side_effect=[
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


if __name__ == "__main__":
    unittest.main()
