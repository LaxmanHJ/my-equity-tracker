"""Unit tests for the pure decision logic in backfill.py (no network, no Turso).

    python3 -m pytest scripts/price_gap_backfill/
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill as bf  # noqa: E402

HOL = {"2026-09-14": "Ganesh Chaturthi", "2026-10-02": "Gandhi Jayanti"}


def bars(closes, start=("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25")):
    return [(d, c, c * 1.01, c * 0.99, c, 1000) for d, c in zip(start, closes)]


def angel_of(stored, factor=1.0, last_close=None, vol=None):
    out = {d: (o * factor, h * factor, l * factor, c * factor, v) for d, o, h, l, c, v in stored}
    if last_close is not None:
        d, o, h, l, c, v = stored[-1]
        out[d] = (o, h, l, last_close, vol if vol is not None else v)
    return out


def test_weekday_sessions_skips_weekends_and_holidays():
    assert bf.weekday_sessions("2026-09-11", "2026-09-16", HOL) == ["2026-09-15", "2026-09-16"]


def test_last_completed_session_never_today():
    assert bf.last_completed_session(HOL, date(2026, 9, 29)) == "2026-09-28"
    assert bf.last_completed_session(HOL, date(2026, 9, 28)) == "2026-09-25"      # Monday -> Friday
    assert bf.last_completed_session(HOL, date(2026, 10, 5)) == "2026-10-01"      # skips Fri holiday


def test_overlap_exact():
    s = bars([100, 101, 102, 103, 104])
    ok, cat, _, _ = bf.classify_overlap(s, angel_of(s))
    assert ok and cat == "exact"


def test_overlap_consistent_ratio_is_corporate_action():
    s = bars([300, 303, 306, 309, 312])
    ok, cat, _, ratio = bf.classify_overlap(s, angel_of(s, factor=2 / 3))   # 1:2 bonus
    assert not ok and cat == "corporate_action" and abs(ratio - 0.66667) < 1e-4


def test_overlap_provisional_last_bar_allowed():
    s = bars([100, 101, 102, 103, 104])
    ok, cat, _, _ = bf.classify_overlap(s, angel_of(s, last_close=105.5, vol=1100))
    assert ok and cat == "provisional_last_bar"


def test_overlap_big_last_bar_difference_is_not_provisional():
    s = bars([100, 101, 102, 103, 104])
    ok, cat, _, _ = bf.classify_overlap(s, angel_of(s, last_close=60))
    assert not ok and cat == "overlap_mismatch"


def test_overlap_missing_day_and_too_few_bars():
    s = bars([100, 101, 102, 103, 104])
    a = angel_of(s)
    del a["2026-09-22"]
    assert bf.classify_overlap(s, a)[1] == "overlap_missing"
    assert bf.classify_overlap(s[:2], angel_of(s[:2]))[1] == "overlap_insufficient"


def test_validate_bars_flags_problems():
    exp = ["2026-09-28", "2026-09-29"]
    good = [("2026-09-28", 10, 11, 9, 10.5, 5), ("2026-09-29", 10, 11, 9, 10.5, 5)]
    assert bf.validate_bars(good, exp, False) == []
    assert any("missing" in p for p in bf.validate_bars(good[:1], exp, False))
    assert any("non-session" in p for p in bf.validate_bars(good + [("2026-09-26", 10, 11, 9, 10, 5)], exp, False))
    assert any("zero" in p for p in bf.validate_bars([("2026-09-28", 0, 11, 9, 10, 5), good[1]], exp, False))
    assert any("high/low" in p for p in bf.validate_bars([("2026-09-28", 10, 9.5, 9, 10, 5), good[1]], exp, False))
    assert any("filler" in p for p in bf.validate_bars([("2026-09-28", 10, 10, 10, 10, 0), good[1]], exp, False))
    assert bf.validate_bars([("2026-09-28", 10, 10, 10, 10, 0), good[1]], exp, True) == []   # index volume is 0


def test_market_sessions_special_and_unannounced():
    syms = [f"S{i}" for i in range(40)]
    windows = {s: "2026-11-04" for s in syms}
    # 2026-11-08 (Sunday, Muhurat) traded by all; 2026-11-06 (Friday) traded by nobody
    fetched = {s: {"2026-11-05", "2026-11-08", "2026-11-09"} for s in syms}
    sess, special, unannounced, _ = bf.market_sessions(windows, fetched, "2026-11-09", {}, {"2026-11-08"})
    assert special == ["2026-11-08"] and unannounced == ["2026-11-06"]
    assert sess == {"2026-11-05", "2026-11-08", "2026-11-09"}


def test_market_sessions_lone_weekend_bar_is_not_a_session():
    syms = [f"S{i}" for i in range(40)]
    windows = {s: "2026-09-25" for s in syms}
    fetched = {s: {"2026-09-28"} for s in syms}
    fetched["S0"] = {"2026-09-27", "2026-09-28"}           # one symbol with a Sunday bar
    sess, special, _, _ = bf.market_sessions(windows, fetched, "2026-09-28", {}, set())
    assert special == [] and sess == {"2026-09-28"}


# ── seed mode ────────────────────────────────────────────────────────────────
SEED_SESS = {"2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"}


def bhav_row(o, h, l, c, v):
    return {"OpnPric": str(o), "HghPric": str(h), "LwPric": str(l), "ClsPric": str(c), "TtlTradgVol": str(v)}


def seed_bars(dates, c=100.0, v=500):
    return {d: (c, c + 1, c - 1, c, v) for d in dates}


def official_for(angel, dates):
    return {d: (bhav_row(*angel[d]) if d in angel else None) for d in dates}


def test_spot_dates_spread_and_small_pools():
    pool = [f"2026-08-{d:02d}" for d in range(3, 29)]
    picks = bf.spot_dates(pool, "2026-08-28")
    assert picks[-1] == "2026-08-28" and len(picks) == 3 and picks[0] < picks[1] < "2026-08-28"
    assert bf.spot_dates(["2026-08-28"], "2026-08-28") == ["2026-08-28"]
    assert bf.spot_dates(["2026-08-27", "2026-08-28"], "2026-08-28") == ["2026-08-27", "2026-08-28"]


def test_bhav_check_categories():
    angel = {"d1": (10, 11, 9, 10.5, 5), "d2": (10, 11, 9, 10.5, 5), "d3": (10, 11, 9, 10.5, 5)}
    official = {"d1": bhav_row(10, 11, 9, 10.5, 5), "d2": bhav_row(10, 11, 9, 10.0, 5), "d3": None,
                "d4": bhav_row(1, 1, 1, 1, 1), "d5": None}
    r = bf.bhav_check(angel, official)
    assert r["matched"] == ["d1"] and len(r["differ"]) == 1 and r["absent"] == ["d3"] and r["nse_only"] == ["d4"]


def test_seed_exclusions_alias_placeholder_holdback():
    overrides = {"HEG": {"angel_symbol": "HEGAM-EQ", "nse_symbol": "HEGAM"},
                 "HFCL": {"angel_symbol": "HFCL-BE", "nse_symbol": "HFCL"}}
    out = bf.seed_exclusions(["HEGAM", "DUMMYHEG", "TRENT", "CLEANMAX", "HFCL", "ODDNAME"],
                             overrides, {"TRENT": {}}, {"ODDNAME": "DUM999A01011", "CLEANMAX": "INE0ABC01011"})
    assert out["HEGAM"][0] == "alias" and "HEG" in out["HEGAM"][1]
    assert out["DUMMYHEG"][0] == "placeholder" and out["ODDNAME"][0] == "placeholder"
    assert out["TRENT"][0] == "held_back"
    assert "CLEANMAX" not in out and "HFCL" not in out       # HFCL-BE strips to HFCL = itself, not an alias


def test_seed_decision_good_path():
    angel = seed_bars(sorted(SEED_SESS))
    ok, cat, _, info = bf.seed_decision(angel, official_for(angel, ["2026-08-04", "2026-08-07"]),
                                        SEED_SESS, "2026-08-03", "2026-08-07", [])
    assert ok and cat == "seed" and info["sessions"] == 5 and not info["listed_in_window"]


def test_seed_decision_listed_inside_window_is_ok():
    angel = seed_bars(["2026-08-06", "2026-08-07"])
    official = official_for(angel, ["2026-08-04", "2026-08-07"])        # NSE has no row on 08-04 either
    ok, _, _, info = bf.seed_decision(angel, official, SEED_SESS, "2026-08-03", "2026-08-07", [])
    assert ok and info["listed_in_window"] and info["first"] == "2026-08-06"


def test_seed_decision_corporate_action_in_window_skips():
    angel = seed_bars(sorted(SEED_SESS))
    ok, cat, why, _ = bf.seed_decision(angel, official_for(angel, ["2026-08-07"]), SEED_SESS,
                                       "2026-08-03", "2026-08-07", [("2026-08-05", "Face Value Split")])
    assert not ok and cat == "corporate_action_in_window" and "--seed-from" in why


def test_seed_decision_angel_missing_early_history():
    angel = seed_bars(["2026-08-06", "2026-08-07"])
    official = {"2026-08-04": bhav_row(90, 91, 89, 90, 400), **official_for(angel, ["2026-08-07"])}
    ok, cat, _, _ = bf.seed_decision(angel, official, SEED_SESS, "2026-08-03", "2026-08-07", [])
    assert not ok and cat == "seed_incomplete"


def test_seed_decision_price_mismatch_and_unverified_and_gaps():
    angel = seed_bars(sorted(SEED_SESS))
    off = official_for(angel, ["2026-08-04", "2026-08-07"])
    off["2026-08-04"] = bhav_row(100, 101, 99, 50, 500)                   # back-adjusted vs raw
    assert bf.seed_decision(angel, off, SEED_SESS, "2026-08-03", "2026-08-07", [])[1] == "official_price_mismatch"
    assert bf.seed_decision(angel, {}, SEED_SESS, "2026-08-03", "2026-08-07", [])[1] == "unverified"
    one = official_for(angel, ["2026-08-07"])                              # 1 match when 2 dates were loaded? -> need 1
    assert bf.seed_decision(angel, one, SEED_SESS, "2026-08-03", "2026-08-07", [])[0]
    gappy = seed_bars(["2026-08-03", "2026-08-05", "2026-08-06", "2026-08-07"])
    assert bf.seed_decision(gappy, official_for(gappy, ["2026-08-05", "2026-08-07"]), SEED_SESS,
                            "2026-08-03", "2026-08-07", [])[1] == "validation"
    assert bf.seed_decision({}, {}, SEED_SESS, "2026-08-03", "2026-08-07", [])[1] == "no_bars"
