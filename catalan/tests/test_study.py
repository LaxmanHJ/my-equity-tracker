"""catalan-v2 analysis: each Amendment 1 rule on synthetic data."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from catalan.analysis import study
from catalan.scoring.scorer import ARM_HEADLINE, ARM_PDF, arm_hash

MODEL = "gemini-3.5-flash-lite"
UNI = frozenset({"AAA", "BBB", "CCC", "DDD", "EEE"})


def weekdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = weekdays(date(2026, 9, 1), 60)


def test_explicit_cost_at_one_lakh_is_about_8_bps():
    assert study.explicit_cost_bps(100_000) == pytest.approx(8.24, abs=0.01)


def test_abdi_ranaldo_needs_enough_pairs_and_floors_at_zero():
    flat = [(100.0, 100.0, 100.0)] * 20
    assert study.abdi_ranaldo_bps(flat) == 0.0
    assert study.abdi_ranaldo_bps([None] * 6 + flat[:14]) is None, "13 pairs < 15"
    bouncy = [(101.0, 99.0, 101.0 if i % 2 else 99.0) for i in range(20)]
    assert study.abdi_ranaldo_bps(bouncy) > 0


def _anns(rows):
    return pd.DataFrame([{"id": i, "symbol": s, "announced_at": t, "headline": f"{s} filing {i}",
                          "category": "Press Release", "attachment_size": None}
                         for i, (s, t) in enumerate(rows, 1)])


def test_eligibility_overnight_weekend_universe_and_window():
    anns = _anns([("AAA", "2026-10-30T18:00:00"),   # Fri evening → Mon 11-02
                  ("BBB", "2026-10-31T11:00:00"),   # Sat daytime → overnight → Mon
                  ("CCC", "2026-11-02T11:00:00"),   # weekday market hours → excluded
                  ("ZZZ", "2026-10-30T18:00:00"),   # not in universe
                  ("DDD", "2026-10-20T18:00:00")])  # before the window
    sessions = weekdays(date(2026, 10, 1), 30)
    e = study.eligible_announcements(anns, sessions, UNI, "2026-10-26", "2026-11-30")
    assert sorted(e["symbol"]) == ["AAA", "BBB"]
    assert set(e["session"]) == {date(2026, 11, 2)}


def test_pdf_verdict_wins_fallback_is_flagged_and_unscored_are_dropped():
    eligible = pd.DataFrame({"id": [1, 2, 3], "symbol": ["AAA", "BBB", "CCC"],
                             "session": [SESSIONS[30]] * 3})
    scores = pd.DataFrame([
        (1, MODEL, arm_hash(ARM_PDF), 0.0, 1), (1, MODEL, arm_hash(ARM_HEADLINE), 0.0, -1),
        (2, MODEL, arm_hash(ARM_HEADLINE), 0.0, -1),
        (3, MODEL, arm_hash(ARM_HEADLINE), None, 1),          # temperature NULL: v1 row, ignored
    ], columns=["announcement_id", "model_id", "prompt_hash", "temperature", "score"])
    v, dropped = study.pick_verdicts(eligible, scores, MODEL)
    assert dict(zip(v["id"], v["verdict"])) == {1: 1, 2: -1}
    assert list(v["fallback"]) == [False, True] and dropped == 1


def _panel(symbols, sessions, drift=0.0, init=0.0):
    rows = []
    for s in symbols:
        for i, d in enumerate(sessions):
            c = 100.0 + (i % 2)
            rows.append({"symbol": s, "date": d, "drift_ret": drift, "init_ret": init,
                         "adv_20d": 1e7 * (1 + sorted(symbols).index(s)), "high": c + 1,
                         "low": c - 1, "close": c})
    return pd.DataFrame(rows)


def test_stock_sessions_validity_spread_and_buckets():
    panel = _panel(sorted(UNI), SESSIONS)
    ss = study.stock_sessions(panel, SESSIONS, UNI, [SESSIONS[30]])
    assert ss["valid"].all() and ss["spread_bps"].notna().all()
    assert sorted(ss["bucket"]) == [1, 2, 3, 4, 5], "one stock per quintile, 1 = least liquid"
    young = study.stock_sessions(panel, SESSIONS, UNI, [SESSIONS[10]])
    assert not young["valid"].any(), "fewer than 20 prior sessions → not valid"


def test_empty_basket_earns_zero_with_zero_cost_and_still_counts():
    panel = _panel(sorted(UNI), SESSIONS, drift=0.01)
    ss = study.stock_sessions(panel, SESSIONS, UNI, SESSIONS[25:30])
    series = study.daily_series(ss, {}, len(UNI), SESSIONS[25])
    assert len(series) == 5 and (series["e"] == 0).all() and (series["c"] == 0).all()


def test_excess_is_basket_minus_universe_and_cost_is_charged():
    panel = _panel(sorted(UNI), SESSIONS)
    panel.loc[panel["symbol"] == "AAA", "drift_ret"] = 0.02
    ss = study.stock_sessions(panel, SESSIONS, UNI, [SESSIONS[30]])
    series = study.daily_series(ss, {("AAA", SESSIONS[30]): 1.0}, len(UNI), SESSIONS[30])
    row = series.iloc[0]
    assert row["e"] == pytest.approx(0.02 - 0.02 / 5)
    assert row["c"] > study.explicit_cost_bps() / 1e4 and row["n"] == pytest.approx(row["e"] - row["c"])


def test_a_session_under_80_percent_coverage_is_not_usable():
    panel = _panel(["AAA", "BBB", "CCC"], SESSIONS)            # 3 of 5 = 60%
    ss = study.stock_sessions(panel, SESSIONS, UNI, [SESSIONS[30]])
    assert study.daily_series(ss, {}, len(UNI), SESSIONS[30]).empty


def test_c0_counts_only_sessions_with_both_baskets():
    s = pd.DataFrame({"c0_obs": [True] * 40 + [False] * 5, "c0_hit": [True] * 29 + [False] * 16})
    r = study.evaluate_c0(s)
    assert r["observations"] == 40 and r["hit_rate"] == pytest.approx(29 / 40) and r["pass"]
    assert not study.evaluate_c0(s.head(30))["pass"], "fewer than 40 observations never passes"


def test_c1c2_on_a_strong_series_passes_and_a_flat_one_fails():
    import numpy as np
    rng = np.random.default_rng(7)
    def series(mu):
        n = rng.normal(mu, 0.01, 120)
        d = {"e": n + 0.0015, "n": n, "yes": [20] * 120, "n_ex_b1": n}
        d.update({f"n_b{k}": n + rng.normal(0, 0.002, 120) for k in range(1, 6)})
        return pd.DataFrame(d)
    strong = study.evaluate_c1c2(series(0.004))
    assert strong["c1"]["pass"] and strong["c2"]["pass"] and not strong["bucket1_only_negative"]
    flat = study.evaluate_c1c2(series(0.0))
    assert not flat["c2"]["pass"]


def test_the_gate_refuses_before_enough_sessions_and_writes_nothing(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(study, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(study, "run", lambda *a, **k: {"series": pd.DataFrame(
        {"c0_obs": [True] * 10, "c0_hit": [True] * 10}), "eligible": 0,
        "dropped_no_verdict": 0, "fallback_share": 0.0})
    monkeypatch.setattr("catalan.data.panel.trading_sessions", lambda a, b: SESSIONS)
    monkeypatch.setattr(sys, "argv", ["study", "--gate", "C0"])
    with pytest.raises(SystemExit, match="NO-PEEK"):
        study.main()
    assert not (tmp_path / "C0.json").exists()


# ── Verifier review 7 ─────────────────────────────────────────────────────────

def test_filings_past_the_last_session_are_skipped_not_crashed_on():
    """B1: after-close filings on the last priced day attribute beyond the calendar."""
    sessions = weekdays(date(2026, 10, 1), 20)
    last = sessions[-1]
    anns = _anns([("AAA", f"{last}T18:00:00"), ("BBB", f"{last}T08:30:00"),
                  ("CCC", f"{sessions[-2]}T18:00:00")])
    e = study.eligible_announcements(anns, sessions, UNI, "2026-10-01")
    assert sorted(e["symbol"]) == ["BBB", "CCC"]


def test_bucket_uses_adv_before_t_never_day_t():
    """S1: a news-day volume spike must not move the stock into a higher bucket that day."""
    panel = _panel(sorted(UNI), SESSIONS)
    t = SESSIONS[30]
    panel.loc[(panel["symbol"] == "AAA") & (panel["date"] == t), "adv_20d"] = 1e12
    ss = study.stock_sessions(panel, SESSIONS, UNI, [t])
    assert ss.set_index("symbol").loc["AAA", "bucket"] == 1, "still the least liquid on t"


def test_c1c2_refuses_without_a_c0_pass(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(study, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["study", "--gate", "C1C2"])
    with pytest.raises(SystemExit, match="C0 has not passed"):
        study.main()
    (tmp_path / "C0.json").write_text('{"pass": false}')
    with pytest.raises(SystemExit, match="C0 has not passed"):
        study.main()
