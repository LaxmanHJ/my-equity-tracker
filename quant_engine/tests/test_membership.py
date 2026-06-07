"""
Assertion tests for P2-c — point-in-time index-membership registry
(``quant_engine/data/membership.py``).

The survivorship-bias fix only works if PIT queries return the right
answers. These tests verify the registry's contains / members_on /
all_symbols semantics on synthetic intervals, the CSV parser's date-
format tolerance, the validator's coverage of bad-interval cases, and
the binary-search correctness on multi-spell symbols (rejoiners).

No DB; everything works on in-memory data + tmp-path CSV fixtures.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from quant_engine.data.membership import (
    MembershipInterval,
    MembershipRegistry,
    load_membership_from_csv,
    validate_intervals,
)


# ── Construction & PIT semantics ────────────────────────────────────────────
def test_contains_inside_interval_returns_true():
    iv = MembershipInterval("NIFTY200", "RELIANCE", date(2020, 1, 1), date(2025, 12, 31))
    reg = MembershipRegistry([iv])
    assert reg.contains("RELIANCE", date(2023, 6, 15))


def test_contains_on_boundary_dates_inclusive():
    """Membership is inclusive on BOTH ends — a stock added on date X is
    a member on X; a stock removed on date Y is a member on Y, not Y+1."""
    iv = MembershipInterval("NIFTY200", "ABC", date(2020, 1, 1), date(2025, 12, 31))
    reg = MembershipRegistry([iv])
    assert reg.contains("ABC", date(2020, 1, 1)), "effective_from is inclusive"
    assert reg.contains("ABC", date(2025, 12, 31)), "effective_to is inclusive"
    assert not reg.contains("ABC", date(2019, 12, 31)), "day before from must be excluded"
    assert not reg.contains("ABC", date(2026, 1, 1)), "day after to must be excluded"


def test_open_interval_means_still_a_member():
    iv = MembershipInterval("NIFTY200", "XYZ", date(2020, 1, 1), None)
    reg = MembershipRegistry([iv])
    assert reg.contains("XYZ", date(2030, 1, 1)), \
        "effective_to=None must mean 'still a member forever forward'"


def test_unknown_symbol_returns_false():
    reg = MembershipRegistry([
        MembershipInterval("NIFTY200", "A", date(2020, 1, 1), None),
    ])
    assert not reg.contains("NOPE", date(2024, 1, 1))


def test_members_on_returns_only_active_symbols():
    intervals = [
        MembershipInterval("NIFTY200", "A", date(2020, 1, 1), date(2023, 12, 31)),
        MembershipInterval("NIFTY200", "B", date(2022, 1, 1), None),
        MembershipInterval("NIFTY200", "C", date(2024, 6, 1), None),
    ]
    reg = MembershipRegistry(intervals)
    assert reg.members_on(date(2021, 1, 1)) == {"A"}
    assert reg.members_on(date(2023, 1, 1)) == {"A", "B"}
    assert reg.members_on(date(2024, 6, 1)) == {"B", "C"}
    assert reg.members_on(date(2025, 1, 1)) == {"B", "C"}


def test_all_symbols_includes_delisted_members():
    """The whole point of survivorship correction: even retired symbols
    must show up in ``all_symbols`` so we can backfill price_history for
    them."""
    intervals = [
        MembershipInterval("NIFTY200", "STILL_HERE", date(2020, 1, 1), None),
        MembershipInterval("NIFTY200", "DELISTED_2021", date(2018, 1, 1), date(2021, 6, 30)),
        MembershipInterval("NIFTY200", "MERGED_2023", date(2018, 1, 1), date(2023, 12, 31)),
    ]
    reg = MembershipRegistry(intervals)
    assert reg.all_symbols() == {"STILL_HERE", "DELISTED_2021", "MERGED_2023"}


# ── Multi-spell (rejoiner) symbols — proper binary search ───────────────────
def test_contains_handles_two_disjoint_spells_for_same_symbol():
    """A symbol can leave the index and rejoin later. Both spells must
    answer correctly. This exercises the binary-search-on-from-dates path."""
    intervals = [
        MembershipInterval("NIFTY200", "REJOINER", date(2018, 1, 1), date(2020, 12, 31)),
        MembershipInterval("NIFTY200", "REJOINER", date(2023, 1, 1), None),
    ]
    reg = MembershipRegistry(intervals)
    assert reg.contains("REJOINER", date(2019, 1, 1))    # in first spell
    assert not reg.contains("REJOINER", date(2022, 1, 1))  # in the gap
    assert reg.contains("REJOINER", date(2024, 1, 1))    # in second spell
    assert not reg.contains("REJOINER", date(2017, 1, 1))  # before first spell


def test_contains_handles_three_spells_for_same_symbol():
    """Edge case: a symbol with 3+ disjoint spells — must find the right
    spell via binary search, not linear scan with off-by-one."""
    intervals = [
        MembershipInterval("NIFTY200", "BUSY", date(2010, 1, 1), date(2012, 12, 31)),
        MembershipInterval("NIFTY200", "BUSY", date(2015, 1, 1), date(2017, 12, 31)),
        MembershipInterval("NIFTY200", "BUSY", date(2020, 1, 1), date(2023, 12, 31)),
    ]
    reg = MembershipRegistry(intervals)
    # Inside each spell.
    assert reg.contains("BUSY", date(2011, 6, 1))
    assert reg.contains("BUSY", date(2016, 6, 1))
    assert reg.contains("BUSY", date(2022, 6, 1))
    # Inside each gap.
    assert not reg.contains("BUSY", date(2014, 6, 1))
    assert not reg.contains("BUSY", date(2019, 6, 1))
    assert not reg.contains("BUSY", date(2024, 6, 1))


# ── Index-name namespacing ──────────────────────────────────────────────────
def test_contains_respects_index_namespace():
    intervals = [
        MembershipInterval("NIFTY200", "X", date(2020, 1, 1), None),
        MembershipInterval("NIFTY50", "Y", date(2020, 1, 1), None),
    ]
    reg = MembershipRegistry(intervals)
    assert reg.contains("X", date(2024, 1, 1), index_name="NIFTY200")
    assert not reg.contains("X", date(2024, 1, 1), index_name="NIFTY50")
    assert reg.contains("Y", date(2024, 1, 1), index_name="NIFTY50")
    assert not reg.contains("Y", date(2024, 1, 1), index_name="NIFTY200")
    assert reg.all_symbols("NIFTY200") == {"X"}
    assert reg.all_symbols("NIFTY50") == {"Y"}


# ── Validator coverage ─────────────────────────────────────────────────────
def test_validator_flags_inverted_intervals():
    bad = [MembershipInterval("NIFTY200", "X", date(2024, 1, 1), date(2020, 1, 1))]
    errs = validate_intervals(bad)
    assert any("inverted" in e for e in errs)


def test_validator_flags_duplicate_keys():
    bad = [
        MembershipInterval("NIFTY200", "X", date(2020, 1, 1), date(2022, 12, 31)),
        MembershipInterval("NIFTY200", "X", date(2020, 1, 1), date(2023, 12, 31)),
    ]
    errs = validate_intervals(bad)
    assert any("duplicate" in e for e in errs)


def test_validator_flags_overlapping_intervals_for_same_symbol():
    """A symbol cannot be a member of the same index twice simultaneously."""
    bad = [
        MembershipInterval("NIFTY200", "X", date(2020, 1, 1), date(2022, 12, 31)),
        MembershipInterval("NIFTY200", "X", date(2022, 6, 1), date(2024, 12, 31)),
    ]
    errs = validate_intervals(bad)
    assert any("overlapping" in e for e in errs)


def test_validator_passes_clean_data():
    good = [
        MembershipInterval("NIFTY200", "A", date(2020, 1, 1), date(2022, 12, 31)),
        MembershipInterval("NIFTY200", "A", date(2023, 1, 1), None),  # rejoiner
        MembershipInterval("NIFTY200", "B", date(2021, 1, 1), None),
    ]
    assert validate_intervals(good) == []


def test_registry_construction_rejects_invalid_data():
    """The registry's constructor must refuse to build from data that fails
    validate_intervals — otherwise downstream PIT answers would be
    nonsensical."""
    bad = [MembershipInterval("NIFTY200", "X", date(2024, 1, 1), date(2020, 1, 1))]
    with pytest.raises(ValueError, match="invalid intervals"):
        MembershipRegistry(bad)


# ── CSV loader ──────────────────────────────────────────────────────────────
def _write_csv(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "membership.csv"
    p.write_text(body)
    return p


def test_csv_loader_parses_standard_format(tmp_path):
    body = (
        "symbol,effective_from,effective_to\n"
        "RELIANCE,2018-01-01,\n"
        "DHFL,2018-01-01,2019-09-25\n"
    )
    rows = load_membership_from_csv(_write_csv(tmp_path, body))
    assert len(rows) == 2
    assert rows[0].symbol == "RELIANCE"
    assert rows[0].effective_to is None
    assert rows[1].symbol == "DHFL"
    assert rows[1].effective_to == date(2019, 9, 25)


def test_csv_loader_accepts_multiple_date_formats(tmp_path):
    body = (
        "symbol,effective_from,effective_to\n"
        "A,2020-01-01,\n"
        "B,2020/01/02,\n"
        "C,01-Jan-2020,\n"
        "D,01-01-2020,\n"
    )
    rows = load_membership_from_csv(_write_csv(tmp_path, body))
    expected = date(2020, 1, 1)
    assert rows[0].effective_from == expected
    assert rows[1].effective_from == date(2020, 1, 2)
    assert rows[2].effective_from == expected
    assert rows[3].effective_from == expected


def test_csv_loader_uses_default_index_when_column_missing(tmp_path):
    body = (
        "symbol,effective_from,effective_to\n"
        "X,2020-01-01,\n"
    )
    rows = load_membership_from_csv(_write_csv(tmp_path, body), default_index="NIFTY50")
    assert rows[0].index_name == "NIFTY50"


def test_csv_loader_honours_explicit_index_column(tmp_path):
    body = (
        "index_name,symbol,effective_from,effective_to\n"
        "BANKNIFTY,HDFCBANK,2020-01-01,\n"
    )
    rows = load_membership_from_csv(_write_csv(tmp_path, body), default_index="NIFTY200")
    assert rows[0].index_name == "BANKNIFTY", \
        "explicit index_name column must override default"


def test_csv_loader_raises_on_unparseable_date(tmp_path):
    body = (
        "symbol,effective_from,effective_to\n"
        "X,not-a-date,\n"
    )
    with pytest.raises(ValueError, match="line 2"):
        load_membership_from_csv(_write_csv(tmp_path, body))


def test_csv_loader_raises_on_blank_symbol(tmp_path):
    body = (
        "symbol,effective_from,effective_to\n"
        ",2020-01-01,\n"
    )
    with pytest.raises(ValueError, match="line 2"):
        load_membership_from_csv(_write_csv(tmp_path, body))


# ── End-to-end story: CSV → registry → PIT query ────────────────────────────
def test_end_to_end_csv_to_pit_query(tmp_path):
    """Realistic happy-path: ingest 4 symbols from CSV, query the registry
    for members on 3 different historical dates."""
    body = (
        "symbol,effective_from,effective_to,source\n"
        "RELIANCE,2018-01-01,,CMIE\n"
        "DHFL,2018-01-01,2019-09-25,NSE_CA_archive\n"
        "ADANIENT,2018-01-01,,nifty_indices\n"
        "YESBANK,2018-01-01,2024-09-30,NSE_CA_archive\n"
    )
    rows = load_membership_from_csv(_write_csv(tmp_path, body))
    reg = MembershipRegistry(rows)

    # 2019: all four are members (DHFL still listed)
    members_2019 = reg.members_on(date(2019, 1, 1))
    assert members_2019 == {"RELIANCE", "DHFL", "ADANIENT", "YESBANK"}

    # 2020: DHFL has been delisted
    members_2020 = reg.members_on(date(2020, 1, 1))
    assert members_2020 == {"RELIANCE", "ADANIENT", "YESBANK"}

    # 2025: YESBANK also delisted; only the two survivors remain
    members_2025 = reg.members_on(date(2025, 1, 1))
    assert members_2025 == {"RELIANCE", "ADANIENT"}

    # all_symbols must include the delisted ones — that's the whole point
    assert reg.all_symbols() == {"RELIANCE", "DHFL", "ADANIENT", "YESBANK"}
