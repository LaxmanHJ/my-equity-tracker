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
            prev = r.get("PrvsClsgPric")
        else:
            sym, series = r.get("SYMBOL", ""), r.get("SERIES", "")
            o, h, l, c = r.get("OPEN"), r.get("HIGH"), r.get("LOW"), r.get("CLOSE")
            vol, isin = r.get("TOTTRDQTY"), r.get("ISIN", "")
            prev = r.get("PREVCLOSE")
        if series != "EQ" or sym not in targets:
            continue
        try:
            # prev_close is captured but does NOT identify corporate actions —
            # see the warning on adjust_for_corporate_actions(). NSE does not
            # restate PREVCLOSE on the ex-date (verified: FINPIPE 2021-04-15,
            # close 698.50 -> 143.65 on a 1:5 split with prev_close still 698.50).
            out[sym] = {"date": d.isoformat(), "open": float(o), "high": float(h),
                        "low": float(l), "close": float(c),
                        "prev_close": float(prev) if prev not in (None, "") else None,
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


def assert_safe_targets(targets: set[str], conn) -> None:
    """
    Refuse to run against symbols that already have substantial price history.

    ``write()`` does DELETE-then-insert per symbol, and bhavcopy OHLC is
    UNADJUSTED. Pointing this at a symbol whose bars came from the Angel feed
    silently replaces good split/bonus-adjusted history with unadjusted bars —
    a corruption that surfaces much later as a phantom gap-down in every
    momentum feature. Bhavcopy is for names Angel can't serve (delisted,
    suspended), so an existing deep history means the wrong list was passed.

    Override deliberately with --force-overwrite when a re-backfill is intended.
    """
    if not targets:
        return
    placeholders = ",".join("?" for _ in targets)
    rows = conn.execute(
        f"SELECT symbol, COUNT(*) n FROM price_history WHERE symbol IN ({placeholders}) "
        f"GROUP BY symbol HAVING n >= 100",
        sorted(targets),
    ).fetchall()
    if rows:
        listed = ", ".join(f"{r[0]}({r[1]})" for r in rows[:15])
        raise SystemExit(
            f"Refusing to overwrite {len(rows)} symbol(s) with existing deep history: {listed}"
            f"{' …' if len(rows) > 15 else ''}\n"
            "Bhavcopy writes UNADJUSTED bars and DELETEs first. Pass only delisted/"
            "missing names, or re-run with --force-overwrite if this is intended."
        )


def adjust_for_corporate_actions(rows: list, tol: float = 0.02) -> tuple[list, int]:
    """
    Back-adjust one symbol's bars for splits/bonuses using the exchange's own
    restated previous close. Returns (adjusted_rows, n_actions_found).

    Bhavcopy prices are RAW. On the ex-date of a 1:10 split the close drops ~90%
    with no economic loss, which reads as a -90% return. That is not cosmetic:
    it contaminates fwd_ret_20d, and because companies split AFTER appreciating,
    "high momentum" predicts the phantom crash. In the 2026-08-04 R2 gate run
    that manufactured an ml_regression long-short Sharpe of 1.382 (DSR 0.974,
    PASS) which collapsed to 0.625 (DSR 0.000) once these symbols were masked.
    The entire apparent edge was this.

    *** BROKEN — DO NOT USE. Kept only as a record of a failed approach. ***

    The premise was that NSE restates PREVCLOSE on the ex-date, making
    ``prev_close(t) / close(t-1)`` the exact adjustment ratio. **It does not.**
    Verified counter-example: FINPIPE on 2021-04-15 closed 698.50 -> 143.65 on a
    1:5 split with prev_close still reading the raw 698.50, ratio 1.0 — invisible
    to this method. ALKYLAMINE 2021-05-11 behaves the same.

    Run against real data on 2026-08-05 it reported "630 corporate actions across
    240 symbols" while missing every actual split. Those 630 were almost certainly
    trading gaps where ``rows[i-1]`` was not the previous session, so the function
    applied 630 spurious adjustments AND left the real splits in place — strictly
    worse than doing nothing. The abort check in the chain caught it before the
    corrupted prices reached a gate run.

    The working fix was different: most "delisted" names are still listed and
    Angel serves them with proper adjustment. Only 76 of 296 were genuinely
    unavailable, and those are kept at raw published prices and disclosed. If
    adjustment is ever needed for that tail, use an authoritative corporate-action
    feed (NSE CAS archive), not a price-ratio heuristic — on a dying company a
    real -50% collapse and a 1:2 split are indistinguishable.
    """
    if len(rows) < 2:
        return rows, 0

    # Walk backward accumulating the factor, so earlier bars get every later action.
    factors = [1.0] * len(rows)
    cum = 1.0
    n_actions = 0
    for i in range(len(rows) - 1, 0, -1):
        prev_close = rows[i].get("prev_close")
        last_close = rows[i - 1].get("close")
        if prev_close and last_close and last_close > 0:
            ratio = prev_close / last_close
            if abs(ratio - 1.0) > tol:
                cum *= ratio
                n_actions += 1
        factors[i - 1] = cum

    if n_actions == 0:
        return rows, 0

    adjusted = []
    for row, f in zip(rows, factors):
        r = dict(row)
        if f != 1.0:
            for k in ("open", "high", "low", "close"):
                if r.get(k) is not None:
                    r[k] = r[k] * f
            # Share count moves inversely to price; keep volume economically comparable.
            if r.get("volume"):
                r["volume"] = int(r["volume"] / f) if f > 0 else r["volume"]
        adjusted.append(r)
    return adjusted, n_actions


def _write_one(conn, sym: str, rows: list, chunk: int, replace: bool = False) -> None:
    """
    Insert one symbol's bars, writing ONLY the dates not already stored.

    Was DELETE-then-reinsert. That is correct but ruinously expensive against a
    metered database: refreshing a symbol with 2,400 bars cost 2,400 deletes plus
    2,400 inserts, whether or not anything had changed. Rewriting 296 symbols
    three times during the 2026-08 NIFTY500 migration burned several million row
    writes and exhausted the Turso free tier's 10M/month allowance.

    Now the existing dates are read first (reads are ~50x cheaper than writes on
    every metered plan) and only genuinely new rows are sent. A no-op refresh
    costs zero writes; a daily top-up costs one row.

    `replace=True` restores the old destructive behaviour for the case it was
    actually meant for: correcting prices already stored, e.g. a split
    adjustment. Callers that are fixing data must opt in explicitly.
    """
    if not rows:
        return

    if replace:
        conn.execute("DELETE FROM price_history WHERE symbol = ?", [sym])
        pending = rows
    else:
        cur = conn.execute("SELECT date FROM price_history WHERE symbol = ?", [sym])
        have = {r[0] for r in cur.fetchall()}
        pending = [b for b in rows if b["date"] not in have]
        if not pending:
            logger.debug("%s: already current, 0 writes", sym)
            return

    params = [(sym, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"])
              for b in pending]
    for i in range(0, len(params), chunk):              # bound request size
        conn.executemany(
            "INSERT OR IGNORE INTO price_history (symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            params[i:i + chunk],
        )
    conn.commit()


def write(bars: dict[str, list], chunk: int = 500, attempts: int = 4,
          replace: bool = False) -> list[str]:
    """
    Write collected bars to price_history, one symbol at a time.

    Retries per symbol and keeps going on persistent failure, instead of letting
    one bad round trip abort the run. This is not hypothetical: a single Turso
    30s ReadTimeout during executemany killed a 296-symbol backfill after 9
    symbols, discarding ~2h of work. A job that makes hundreds of network round
    trips will hit a blip; the question is only whether it loses everything when
    it does.

    Each symbol is DELETE-then-insert, so a retry is idempotent — a half-written
    symbol from a failed attempt is cleared by the next attempt's DELETE.

    Returns the list of symbols that could not be written.
    """
    from quant_engine.data.turso_client import connect
    conn = connect()
    failed: list[str] = []
    todo = [(s, r) for s, r in sorted(bars.items()) if r]

    for n, (sym, rows) in enumerate(todo, 1):
        for attempt in range(1, attempts + 1):
            try:
                _write_one(conn, sym, rows, chunk, replace=replace)
                logger.info("wrote %s: %d bars  (%d/%d)", sym, len(rows), n, len(todo))
                break
            except Exception as exc:  # noqa: BLE001 — network/timeout/5xx are all retryable here
                if attempt == attempts:
                    failed.append(sym)
                    logger.error("GIVING UP on %s after %d attempts: %s", sym, attempts, exc)
                    break
                backoff = 2 ** attempt
                logger.warning("write %s failed (attempt %d/%d): %s — retrying in %ds",
                               sym, attempt, attempts, exc, backoff)
                time.sleep(backoff)
                try:
                    conn = connect()          # rebuild a possibly-poisoned connection
                except Exception:             # noqa: BLE001
                    pass

    if failed:
        logger.error("%d symbol(s) failed permanently: %s", len(failed), ", ".join(failed))
    return failed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="d0", default="2017-01-01", help="start date YYYY-MM-DD")
    p.add_argument("--to", dest="d1", default="2025-06-30", help="end date YYYY-MM-DD")
    p.add_argument("--symbols", default=None, help="comma list (overrides --file)")
    p.add_argument("--file", default=str(DEFAULT_TARGETS), help="target symbols, one per line")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p.add_argument("--throttle", type=float, default=0.3, help="seconds between requests")
    p.add_argument("--apply", action="store_true", help="write to Turso (else dry-run)")
    p.add_argument("--replace-existing", dest="replace_existing", action="store_true",
                   help="DELETE the symbol's stored bars before inserting. Only for CORRECTING "
                        "prices already in the DB (e.g. a split adjustment). Costs one write per "
                        "existing row; the default incremental path costs zero for unchanged data.")
    p.add_argument("--raw-prices", action="store_true",
                   help="skip split/bonus back-adjustment (default is to adjust). Raw bhavcopy "
                        "prices contain phantom -90%% split days that contaminate forward returns "
                        "— see adjust_for_corporate_actions()")
    p.add_argument("--force-overwrite", action="store_true",
                   help="skip the guard that refuses to replace symbols with existing deep "
                        "history (bhavcopy bars are UNADJUSTED — see assert_safe_targets)")
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

    if not a.raw_prices:
        total_actions = 0
        touched = 0
        for sym in list(bars):
            bars[sym], n = adjust_for_corporate_actions(bars[sym])
            if n:
                total_actions += n
                touched += 1
        logger.info("Corporate-action adjustment: %d action(s) across %d symbol(s)",
                    total_actions, touched)

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
    if not a.force_overwrite:
        from quant_engine.data.turso_client import connect
        with connect() as _c:
            assert_safe_targets(targets, _c)
    logger.info("Writing to Turso …")
    failed = write(bars, replace=a.replace_existing)
    if failed:
        # Non-zero exit so a scripted/CI caller notices, and the list is printed
        # so it can be fed straight back in via --file.
        print(f"\n{len(failed)} symbol(s) FAILED: {', '.join(failed)}")
        return 1
    print("Done — written to price_history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
