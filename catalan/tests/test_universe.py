"""Point-in-time universe join.

The failure this guards against is the expensive one: filtering announcements
against *today's* NIFTY500 roster silently drops the firms that left the index
because they failed. The paper's alpha concentrates in small caps, which is
exactly the population that churns, so survivorship bias here would not be a
rounding error.

Runs against a hand-built registry — no Turso, no network.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache

import pytest

from catalan.data import universe


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    """A three-symbol NIFTY500 with one delisting and one re-entry."""
    from quant_engine.data.membership import MembershipInterval, MembershipRegistry

    intervals = [
        # steady member throughout
        MembershipInterval("NIFTY500", "RELIANCE", date(2018, 1, 1), None),
        # dropped out of the index mid-2022 and never came back
        MembershipInterval("NIFTY500", "FAILEDCO", date(2018, 1, 1), date(2022, 6, 30)),
        # two separate spells — must not be treated as one continuous one
        MembershipInterval("NIFTY500", "INOUT", date(2019, 1, 1), date(2020, 12, 31)),
        MembershipInterval("NIFTY500", "INOUT", date(2023, 1, 1), None),
    ]
    reg = MembershipRegistry.from_iterable(intervals)

    # lru_cache-wrapped so the stand-in has the same cache_clear surface as the
    # real accessor — universe.reset_cache() calls it during teardown.
    @lru_cache(maxsize=1)
    def _fake():
        return reg

    universe.reset_cache()
    monkeypatch.setattr(universe, "_registry", _fake)
    universe.members_on.cache_clear()
    yield
    universe.reset_cache()


def test_member_during_its_spell():
    assert universe.is_member("FAILEDCO", "2022-01-15")
    assert "FAILEDCO" in universe.members_on("2022-01-15")


def test_delisted_symbol_is_excluded_after_its_spell_ends():
    """The whole point: a 2024 query must not see a firm that left in 2022."""
    assert not universe.is_member("FAILEDCO", "2024-01-15")
    assert "FAILEDCO" not in universe.members_on("2024-01-15")


def test_symbol_is_excluded_before_it_ever_joined():
    assert not universe.is_member("INOUT", "2018-06-01")


def test_gap_between_two_spells_is_respected():
    assert universe.is_member("INOUT", "2019-06-01")
    assert not universe.is_member("INOUT", "2021-06-01")   # the gap
    assert universe.is_member("INOUT", "2024-06-01")


def test_spell_end_date_is_inclusive():
    assert universe.is_member("FAILEDCO", "2022-06-30")
    assert not universe.is_member("FAILEDCO", "2022-07-01")


def test_ever_members_includes_the_delisted():
    """This is the right universe for querying price_history — members_on is
    the right filter to apply once you have a date."""
    assert universe.ever_members() == {"RELIANCE", "FAILEDCO", "INOUT"}


def test_accepts_str_date_and_datetime_alike():
    from datetime import datetime

    assert universe.is_member("RELIANCE", "2022-01-15")
    assert universe.is_member("RELIANCE", date(2022, 1, 15))
    assert universe.is_member("RELIANCE", datetime(2022, 1, 15, 18, 30))


def test_members_on_is_memoised_per_date():
    """500k announcements over ~1,900 sessions — without the memo this is
    500k full scans of the registry."""
    universe.members_on.cache_clear()
    universe.members_on("2022-01-15")
    universe.members_on("2022-01-15")
    assert universe.members_on.cache_info().hits >= 1
