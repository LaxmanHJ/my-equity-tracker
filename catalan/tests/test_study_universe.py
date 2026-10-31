"""The catalan-v2 study universe: a frozen file, checked on every load."""
from __future__ import annotations

import pytest

from catalan.data import universe


def test_the_frozen_universe_is_eq_only_with_the_declared_exclusions():
    u = universe.study_universe()
    assert len(u) == 491
    assert {"RELIANCE", "TCS", "INFY"} <= u
    assert not ({"TRENT", "CUB", "HEGAM", "DUMMYHEG"} & u), "declared exclusions"
    assert not ({"HFCL", "MTARTECH", "STLTECH"} & u), "BE series cannot trade intraday"
    assert not ({"EMBASSY", "BIRET", "BAGMANE"} & u), "REIT units are not company shares"


def test_an_edited_universe_file_fails_loudly(tmp_path, monkeypatch):
    bad = tmp_path / "u.csv"
    bad.write_text("Company Name,Industry,Symbol,Series,ISIN Code\nX,Y,TINYCO,EQ,INE000000000\n")
    monkeypatch.setattr(universe, "CATALAN_ROOT", str(tmp_path))
    monkeypatch.setattr(universe, "UNIVERSE_FILE", "u.csv")
    universe.study_universe.cache_clear()
    try:
        with pytest.raises(universe.UniverseFileChanged):
            universe.study_universe()
    finally:
        universe.study_universe.cache_clear()
