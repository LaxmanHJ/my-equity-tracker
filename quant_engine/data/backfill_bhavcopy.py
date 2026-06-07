"""
Backfill OHLCV for DELISTED / distinct-security members from the NSE Bhavcopy
archive — survivorship Tier 3 (the names no broker feed carries anymore:
DHFL, RCOM, the Reliance ADAG trio, Future group, ALBK/ORIENTBANK, HDFC Ltd…).

Why a separate fetcher
----------------------
`backfill-angel-15y.mjs` covers everything still in Angel's scrip master. The
genuinely dead names are gone from Angel — but they still exist in the daily
Bhavcopy snapshots NSE publishes per trading day. We iterate **per date, not per
symbol**: one zip per day holds every stock that traded, so 1 symbol or 30 costs
the same — the cost is "number of trading days," not "number of symbols."

Two archive formats (schema changed mid-2024)
---------------------------------------------
* Legacy (≤ 2024-07-05), covers almost all delistings here:
  https://nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DDMONYYYY}bhav.csv.zip
  cols: SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, ..., TOTTRDQTY, ..., ISIN
* UDiFF (≥ 2024-07-08), needed only for the latest exits (TATAMTRDVR ~Aug-2024,
  ISEC/IDFC ~Oct-2024):
  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
  cols: TckrSymb, SctySrs, OpnPric, HghPric, LwPric, ClsPric, TtlTradgVol, ISIN

Safety
------
* **ISIN lock** — NSE *reuses* delisted tickers for new companies. Each symbol is
  locked to the first ISIN seen; rows with a different ISIN (post-delisting reuse)
  are dropped.
* **Disk cache** — every zip is cached under --cache-dir, so re-runs are resumable
  and idempotent (the single most important practical feature given ~thousands of
  requests against a rate-limited host).
* OHLC here is **unadjusted** (raw) — no split/bonus adjustment. Keep consistent
  with the Angel data's convention before computing cross-action returns.

Usage
-----
    # dry-run, default window, default target file
    python -m quant_engine.data.backfill_bhavcopy --dry-run
    # specific window / symbols
    python -m quant_engine.data.backfill_bhavcopy --from 2017-01-01 --to 2025-06-30 --apply
    python -m quant_engine.data.backfill_bhavcopy --symbols DHFL,RCOM --apply
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DEFAULT_TARGETS = ROOT / "data/membership_sources/tier3_delisted_need_bhavcopy.txt"
DEFAULT_CACHE = ROOT / "data/membership_sources/bhavcopy_cache"

UDIFF_FROM = date(2024, 7, 8)          # NSE switched to UDiFF on this date
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
RETIRE_AFTER = 60                      # consecutive no-show trading days => delisted
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- IO
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*",
                      "Referer": "https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com", timeout=10)   # warm cookies (avoid 403)
    except requests.RequestException as e:
        logger.warning("cookie warm-up failed: %s", e)
    return s


def _url(d: date) -> str:
    if d >= UDIFF_FROM:
        return ("https://nsearchives.nseindia.com/content/cm/"
                f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    mon = MONTHS[d.month - 1]
    return ("https://nsearchives.nseindia.com/content/historical/EQUITIES/"
            f"{d.year}/{mon}/cm{d.day:02d}{mon}{d.year}bhav.csv.zip")


def _fetch_zip(sess: requests.Session, d: date, cache: Path) -> bytes | None:
    """Return raw zip bytes (cached), or None on holiday/404. Sentinel marks holidays."""
    cf = cache / f"{d:%Y%m%d}.zip"
    miss = cache / f"{d:%Y%m%d}.404"
    if cf.exists():
        return cf.read_bytes()
    if miss.exists():
        return None
    for attempt in range(3):
        try:
            r = sess.get(_url(d), timeout=20)
        except requests.RequestException as e:
            logger.warning("%s: %s (retry)", d, e); time.sleep(1.5); continue
        if r.status_code == 200 and r.content[:2] == b"PK":
            cf.write_bytes(r.content); return r.content
        if r.status_code == 404:
            miss.touch(); return None                   # market holiday / weekend
        if r.status_code in (401, 403):
            sess = _session(); time.sleep(1.0); continue  # cookie expired
        time.sleep(1.0)
    logger.warning("%s: giving up after retries", d)
    return None


# ------------------------------------------------------------------------ parse
def _parse(zbytes: bytes, d: date, targets: set[str]) -> dict[str, dict]:
    """{symbol: {date,open,high,low,close,volume,isin}} for target EQ rows on date d."""
    udiff = d >= UDIFF_FROM
    out: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if not name:
            return out
        text = z.read(name).decode("utf-8", "ignore")
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(f or "").strip() for f in (reader.fieldnames or [])]
    for row in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if udiff:
            sym, series = r.get("TckrSymb", ""), r.get("SctySrs", "")
            o, h, l, c = r.get("OpnPric"), r.get("HghPric"), r.get("LwPric"), r.get("ClsPric")
            vol, isin = r.get("TtlTradgVol"), r.get("ISIN", "")
        else:
            sym, series = r.get("SYMBOL", ""), r.get("SERIES", "")
            o, h, l, c = r.get("OPEN"), r.get("HIGH"), r.get("LOW"), r.get("CLOSE")
            vol, isin = r.get("TOTTRDQTY"), r.get("ISIN", "")
        if series != "EQ" or sym not in targets:
            continue
        try:
            out[sym] = {"date": d.isoformat(), "open": float(o), "high": float(h),
                        "low": float(l), "close": float(c),
                        "volume": int(float(vol or 0)), "isin": isin}
        except (TypeError, ValueError):
            continue
    return out


# ------------------------------------------------------------------------- main
def collect(targets: set[str], d0: date, d1: date, cache: Path, throttle: float) -> dict[str, list]:
    cache.mkdir(parents=True, exist_ok=True)
    sess = _session()
    bars: dict[str, list] = {s: [] for s in targets}
    isin_lock: dict[str, str] = {}
    seen_any: set[str] = set()
    noshow: dict[str, int] = {s: 0 for s in targets}
    active = set(targets)

    d, days, hits = d0, 0, 0
    while d <= d1 and active:
        if d.weekday() < 5:                              # skip Sat/Sun outright
            z = _fetch_zip(sess, d, cache)
            if z is not None:
                days += 1
                found = _parse(z, d, active)
                for sym, bar in found.items():
                    isin = bar.pop("isin")
                    if sym not in isin_lock:
                        isin_lock[sym] = isin
                    elif isin and isin != isin_lock[sym]:
                        continue                         # ticker reuse — different company
                    bars[sym].append(bar); seen_any.add(sym); noshow[sym] = 0; hits += 1
                for sym in list(active):                 # retire delisted (post first-appearance)
                    if sym in seen_any and sym not in found:
                        noshow[sym] += 1
                        if noshow[sym] >= RETIRE_AFTER:
                            active.discard(sym)
                if days % 250 == 0:
                    logger.info("…%s | trading-days scanned=%d rows=%d active=%d",
                                d, days, hits, len(active))
                if throttle:
                    time.sleep(throttle)
        d += timedelta(days=1)
    return bars


def write(bars: dict[str, list], chunk: int = 500) -> None:
    from quant_engine.data.turso_client import connect
    conn = connect()
    for sym, rows in sorted(bars.items()):
        if not rows:
            continue
        conn.execute("DELETE FROM price_history WHERE symbol = ?", [sym])
        params = [(sym, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in rows]
        for i in range(0, len(params), chunk):          # bound request size
            conn.executemany(
                "INSERT OR REPLACE INTO price_history (symbol, date, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                params[i:i + chunk],
            )
        conn.commit()
        logger.info("wrote %s: %d bars", sym, len(rows))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="d0", default="2017-01-01", help="start date YYYY-MM-DD")
    p.add_argument("--to", dest="d1", default="2025-06-30", help="end date YYYY-MM-DD")
    p.add_argument("--symbols", default=None, help="comma list (overrides --file)")
    p.add_argument("--file", default=str(DEFAULT_TARGETS), help="target symbols, one per line")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p.add_argument("--throttle", type=float, default=0.3, help="seconds between requests")
    p.add_argument("--apply", action="store_true", help="write to Turso (else dry-run)")
    a = p.parse_args(argv)

    if a.symbols:
        targets = {s.strip().upper() for s in a.symbols.split(",") if s.strip()}
    else:
        targets = {ln.strip().upper() for ln in Path(a.file).read_text().splitlines() if ln.strip()}
    d0 = datetime.fromisoformat(a.d0).date()
    d1 = datetime.fromisoformat(a.d1).date()
    logger.info("Bhavcopy backfill: %d symbols, %s → %s, cache=%s",
                len(targets), d0, d1, a.cache_dir)

    bars = collect(targets, d0, d1, Path(a.cache_dir), a.throttle)

    print(f"\n{'symbol':<14}{'rows':>7}  {'first':<12}{'last':<12}")
    print("-" * 46)
    got = 0
    for sym in sorted(targets):
        rs = bars.get(sym, [])
        if rs:
            got += 1
            print(f"{sym:<14}{len(rs):>7}  {rs[0]['date']:<12}{rs[-1]['date']:<12}")
        else:
            print(f"{sym:<14}{0:>7}  {'— NOT FOUND (verify symbol/window)':<24}")
    print(f"\n{got}/{len(targets)} symbols recovered; "
          f"{sum(len(v) for v in bars.values())} total bars.")

    if not a.apply:
        print("\n(dry-run — pass --apply to write to Turso)")
        return 0
    logger.info("Writing to Turso …")
    write(bars)
    print("Done — written to price_history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
