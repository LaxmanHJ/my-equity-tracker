"""
Tests for market_regime writes — no writer may clobber another's columns.

Regression tests for the 2026-07-27 FII staleness incident: the VIX sync used
`INSERT OR REPLACE INTO market_regime (date, india_vix)`. REPLACE deletes the
conflicting row and reinserts it, so fii_net_cash / dii_net_cash written
moments earlier by the /sync/fii step of the same force-sync pass were reset
to NULL. fii_net_cash then sat 20 days stale and failed the EOD sync gate.

Also covers the mirror gap-fill parser added in the same fix — the rolling
window is what lets a missed day be recovered at all, since NSE's live
endpoint only ever serves the current trading day.
"""
import json
import sqlite3

import pytest

from quant_engine.data.backfill_fii_dii import UPSERT_SQL, parse_mirror_rows
from quant_engine.data.backfill_regime import VIX_UPSERT_SQL


SCHEMA = """
CREATE TABLE market_regime (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL UNIQUE,
    india_vix       REAL,
    fii_net_cash    REAL,
    dii_net_cash    REAL,
    fii_fo_net_long REAL
)
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(SCHEMA)
    yield c
    c.close()


def _row(conn, date="2026-07-27"):
    cur = conn.execute(
        "SELECT india_vix, fii_net_cash, dii_net_cash, fii_fo_net_long "
        "FROM market_regime WHERE date = ?", (date,)
    )
    return cur.fetchone()


class TestVixUpsertPreservesFlows:
    """The exact ordering the force-sync runs: FII first, VIX second."""

    def test_vix_write_keeps_fii_and_dii(self, conn):
        conn.execute(UPSERT_SQL, {"date": "2026-07-27", "fii": -1688.23, "dii": 2329.14})
        conn.execute(VIX_UPSERT_SQL, {"date": "2026-07-27", "vix": 12.66})

        vix, fii, dii, _ = _row(conn)
        assert (vix, fii, dii) == (12.66, -1688.23, 2329.14)

    def test_vix_write_keeps_fo_net_long(self, conn):
        """fii_fo_net_long has its own writer (backfill_fo_oi) — also at risk."""
        conn.execute(
            "INSERT INTO market_regime (date, fii_fo_net_long) VALUES (:date, :val) "
            "ON CONFLICT(date) DO UPDATE SET fii_fo_net_long = excluded.fii_fo_net_long",
            {"date": "2026-07-27", "val": -12345.0},
        )
        conn.execute(VIX_UPSERT_SQL, {"date": "2026-07-27", "vix": 12.66})

        assert _row(conn)[3] == -12345.0

    def test_fii_write_keeps_vix(self, conn):
        """Reverse order — a late FII gap-fill must not drop the day's VIX."""
        conn.execute(VIX_UPSERT_SQL, {"date": "2026-07-27", "vix": 12.66})
        conn.execute(UPSERT_SQL, {"date": "2026-07-27", "fii": -1688.23, "dii": 2329.14})

        vix, fii, dii, _ = _row(conn)
        assert (vix, fii, dii) == (12.66, -1688.23, 2329.14)

    def test_vix_rewrite_updates_in_place(self, conn):
        """Idempotence: re-running the sync overwrites VIX, adds no second row."""
        conn.execute(VIX_UPSERT_SQL, {"date": "2026-07-27", "vix": 12.66})
        conn.execute(VIX_UPSERT_SQL, {"date": "2026-07-27", "vix": 13.10})

        assert conn.execute("SELECT COUNT(*) FROM market_regime").fetchone()[0] == 1
        assert _row(conn)[0] == 13.10

    def test_replace_semantics_still_clobber(self, conn):
        """
        Pins the reason the fix is phrased as ON CONFLICT DO UPDATE. If this
        ever stops holding, the guard comments in the writers can go.
        """
        conn.execute(UPSERT_SQL, {"date": "2026-07-27", "fii": -1688.23, "dii": 2329.14})
        conn.execute(
            "INSERT OR REPLACE INTO market_regime (date, india_vix) VALUES (?, ?)",
            ("2026-07-27", 12.66),
        )
        assert _row(conn)[1] is None


def _mirror_html(rows):
    blob = {"props": {"pageProps": {"FiiDiiData": {"fiiDiiData": rows}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(blob)
        + "</script></body></html>"
    )


class TestMirrorParser:
    def test_parses_comma_formatted_values(self):
        rows = parse_mirror_rows(_mirror_html([
            {"date": "2026-07-27", "fiiNet": "-1,688.23", "diiNet": "2,329.14"},
            {"date": "2026-07-24", "fiiNet": "-3,892.77", "diiNet": "5,453.55"},
        ]))
        assert rows == [
            {"date": "2026-07-27", "fii": -1688.23, "dii": 2329.14},
            {"date": "2026-07-24", "fii": -3892.77, "dii": 5453.55},
        ]

    def test_skips_unparseable_dates_keeps_rest(self):
        rows = parse_mirror_rows(_mirror_html([
            {"date": "not a date", "fiiNet": "1.0", "diiNet": "2.0"},
            {"date": "2026-07-24", "fiiNet": "-3,892.77", "diiNet": "5,453.55"},
        ]))
        assert [r["date"] for r in rows] == ["2026-07-24"]

    @pytest.mark.parametrize("html", [
        "<html><body>no blob here</body></html>",
        _mirror_html([]),
    ])
    def test_raises_rather_than_returning_empty(self, html):
        """A silent [] would look like 'no new data' and re-hide the staleness."""
        with pytest.raises(ValueError):
            parse_mirror_rows(html)

    def test_raises_when_json_shape_changes(self):
        html = (
            '<script id="__NEXT_DATA__">'
            + json.dumps({"props": {"pageProps": {}}})
            + "</script>"
        )
        with pytest.raises(ValueError):
            parse_mirror_rows(html)
