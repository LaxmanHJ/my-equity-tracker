"""Session classification — the boundaries the whole study rests on.

If a mid-session announcement is misclassified as overnight, the "drift" it
predicts is partly the reaction that already happened. That is a lookahead bug
that would produce a large, entirely fake positive result, so the 09:00 and
15:30 IST edges are tested exactly.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from catalan.data.panel import INTRADAY, OVERNIGHT, attribution_date, classify_session


def dt(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss)


@pytest.mark.parametrize("stamp,expected", [
    # after the close → overnight
    (dt(2026, 8, 6, 15, 30, 1), OVERNIGHT),
    (dt(2026, 8, 6, 18, 23, 45), OVERNIGHT),
    (dt(2026, 8, 6, 23, 59, 59), OVERNIGHT),
    # before the 09:00 cutoff → overnight
    (dt(2026, 8, 7, 0, 0, 0), OVERNIGHT),
    (dt(2026, 8, 7, 8, 59, 59), OVERNIGHT),
    # inside the session → intraday
    (dt(2026, 8, 7, 9, 0, 0), INTRADAY),     # exactly 09:00 is already too late
    (dt(2026, 8, 7, 9, 15, 0), INTRADAY),
    (dt(2026, 8, 7, 12, 0, 0), INTRADAY),
    (dt(2026, 8, 7, 15, 30, 0), INTRADAY),   # exactly 15:30 is still in-session
])
def test_classify_session_boundaries(stamp, expected):
    assert classify_session(stamp) == expected


def test_the_1500_1530_vwap_window_is_intraday():
    """NSE's close is a VWAP of 15:00-15:30, so news in that window is inside
    the session it moves — not overnight news for the next one."""
    assert classify_session(dt(2026, 8, 7, 15, 15, 0)) == INTRADAY


def test_after_close_rolls_to_next_day():
    assert attribution_date(dt(2026, 8, 6, 19, 0, 0)) == date(2026, 8, 7)


def test_before_cutoff_stays_same_day():
    assert attribution_date(dt(2026, 8, 7, 7, 30, 0)) == date(2026, 8, 7)


def test_friday_evening_news_lands_on_monday():
    """2026-08-07 is a Friday; the next session is Monday the 10th."""
    sessions = [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    assert attribution_date(dt(2026, 8, 7, 20, 0, 0), sessions) == date(2026, 8, 10)


def test_holiday_is_skipped():
    """A gap in the trading calendar must roll forward, not attribute to a
    day on which no open or close exists."""
    sessions = [date(2026, 8, 6), date(2026, 8, 11)]   # 7th-10th are holidays
    assert attribution_date(dt(2026, 8, 6, 18, 0, 0), sessions) == date(2026, 8, 11)


def test_news_past_the_calendar_raises_rather_than_guessing():
    sessions = [date(2026, 8, 6), date(2026, 8, 7)]
    with pytest.raises(ValueError, match="beyond the last known trading day"):
        attribution_date(dt(2026, 8, 20, 18, 0, 0), sessions)
