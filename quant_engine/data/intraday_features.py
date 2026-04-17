"""
Intraday feature engineering from 15-min candles.

Produces one row per (symbol, date) with three features derived from
intraday bars and the daily price_history ATR:

    overnight_gap         (today_open - prev_close) / prev_close
    intraday_range_ratio  (day_high - day_low) / ATR14_daily
    last_hour_momentum    (last_close - close_at_14_15) / close_at_14_15

Reads from Turso `intraday_candles` and `price_history`.
"""
from __future__ import annotations

import pandas as pd
from quant_engine.data.turso_client import connect


def load_intraday(symbol: str) -> pd.DataFrame:
    """
    Read raw intraday_candles for a symbol.
    Returns a tz-aware DataFrame indexed by IST timestamp.
    """
    conn = connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT ts, open, high, low, close, volume
            FROM intraday_candles
            WHERE symbol = ?
            ORDER BY ts ASC
            """,
            conn,
            params=(symbol,),
        )
    finally:
        conn.close()

    if df.empty:
        return df

    # Angel returns ISO-8601 with +05:30 offset — preserve the tz for reliable
    # "last hour" matching across DST edges (India has no DST but we stay explicit).
    df["ts"] = pd.to_datetime(df["ts"], utc=False)
    df = df.set_index("ts").sort_index()
    return df


def _aggregate_daily_bars(intraday: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse intraday bars into one row per trading date with the raw pieces
    needed for the three features.
    """
    if intraday.empty:
        return pd.DataFrame()

    idx = intraday.index
    hhmm = idx.strftime("%H:%M")
    local_date = idx.strftime("%Y-%m-%d")

    df = intraday.copy()
    df["date"] = local_date
    df["hhmm"] = hhmm

    grouped = df.groupby("date", sort=True)
    daily = pd.DataFrame(
        {
            "day_open": grouped["open"].first(),
            "day_high": grouped["high"].max(),
            "day_low": grouped["low"].min(),
            "day_close": grouped["close"].last(),
        }
    )

    # last bar at or before 14:15 and 15:15 — we need the close of that bar
    # 15-min bars stamp at bar open; 14:15 bar covers 14:15–14:30, 15:15 bar covers 15:15–15:30
    def close_at(h_m: str) -> pd.Series:
        mask = df["hhmm"] == h_m
        if not mask.any():
            return pd.Series(dtype=float)
        return df.loc[mask].groupby("date")["close"].first()

    daily["close_14_15"] = close_at("14:15")
    daily["close_15_15"] = close_at("15:15")

    daily.index = pd.to_datetime(daily.index)
    daily.index.name = "date"
    return daily


def _atr14_from_daily(symbol: str) -> pd.Series:
    """Wilder-style ATR14 from daily price_history, indexed by date."""
    conn = connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, high, low, close
            FROM price_history
            WHERE symbol = ?
            ORDER BY date ASC
            """,
            conn,
            params=(symbol,),
        )
    finally:
        conn.close()

    if df.empty:
        return pd.Series(dtype=float)

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder's smoothing ≈ EMA with alpha = 1/14
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    atr.name = "atr14"
    return atr


def build_intraday_features(symbol: str) -> pd.DataFrame:
    """
    Return a DataFrame with columns [overnight_gap, intraday_range_ratio,
    last_hour_momentum] indexed by date for a symbol.

    Rows where any component is unavailable are dropped.
    """
    intraday = load_intraday(symbol)
    if intraday.empty:
        return pd.DataFrame(columns=["overnight_gap", "intraday_range_ratio", "last_hour_momentum"])

    daily = _aggregate_daily_bars(intraday)
    atr = _atr14_from_daily(symbol)
    if atr.empty:
        return pd.DataFrame(columns=["overnight_gap", "intraday_range_ratio", "last_hour_momentum"])

    # align on date index
    daily = daily.join(atr, how="left")

    prev_close = daily["day_close"].shift(1)
    overnight_gap = (daily["day_open"] - prev_close) / prev_close

    range_ratio = (daily["day_high"] - daily["day_low"]) / daily["atr14"]

    last_hour = (daily["close_15_15"] - daily["close_14_15"]) / daily["close_14_15"]

    out = pd.DataFrame(
        {
            "overnight_gap": overnight_gap,
            "intraday_range_ratio": range_ratio,
            "last_hour_momentum": last_hour,
        }
    )
    out = out.replace([pd.NA, float("inf"), float("-inf")], pd.NA).dropna()
    return out


def build_intraday_features_all(symbols: list[str]) -> pd.DataFrame:
    """Concat build_intraday_features for many symbols, with a `symbol` column."""
    frames = []
    for s in symbols:
        f = build_intraday_features(s)
        if f.empty:
            continue
        f = f.reset_index()
        f["symbol"] = s
        frames.append(f)
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "overnight_gap", "intraday_range_ratio", "last_hour_momentum"])
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "INFY"
    feats = build_intraday_features(sym)
    print(f"{sym}: {len(feats)} feature rows")
    print(feats.tail(10))
    print("\nSummary stats:")
    print(feats.describe())
