#!/usr/bin/env python3
"""
Fill Turso ``price_history`` from each symbol's last stored bar up to a target
session. Source: Angel One daily candles. DRY RUN BY DEFAULT; pass --apply to write.

    python3 scripts/price_gap_backfill/backfill.py               # dry run to last completed session
    python3 scripts/price_gap_backfill/backfill.py --apply       # same, then write

See README.md in this directory for the checks, the report and the known quirks.
Only INSERT OR IGNORE is ever issued, and only into price_history.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import re
import statistics
import subprocess
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
SCOPE_SESSIONS = 20        # "recently active" = has a row in the table's last N sessions
N_OVERLAP = 5              # stored sessions re-fetched and compared per symbol
MIN_OVERLAP = 3            # fewer stored bars than this -> cannot verify -> skip
REL_TOL = 0.001            # 0.1% OHLC tolerance for "equal"
RATIO_SPREAD = 0.002       # a ratio is "consistent" if it varies < 0.2% across overlap days
PROVISIONAL_MAX = 0.03     # a differing LAST stored bar within 3% is a provisional close, not an action
FETCH_LEAD_DAYS = 7        # start the Angel request this many days before the oldest overlap day
BIG_MOVE = 0.18            # daily |return| reported for review
SPECIAL_MIN_COVER = 0.5    # an unlisted date is a real (special) session if >= 50% of symbols have a bar
HOLIDAY_MAX_COVER = 0.1    # a listed weekday is an unannounced holiday if < 10% of symbols have a bar
MIN_DENOM = 20             # below this many symbols, skip cross-sectional session inference
BATCH = 500
GAP_MS = 400               # Angel: <= 2.5 requests/s
SEED_FROM = "2026-08-01"   # --seed default start
SEED_MIN_BHAV = 2          # a seed needs this many official-price matches (fewer only if it listed late)
SEED_SHORT = 20            # seeds with fewer sessions than this are flagged in the report
CA_PATTERN = re.compile(r"split|sub-?division|bonus|demerger|consolidat|capital reduction|"
                        r"reduction of (share )?capital|scheme of arrangement|amalgamation", re.I)
STOP_WORDS = ("quota", "limit", "blocked", "exceed", "forbidden", "payment", "upgrade", "denied")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(msg=""):
    print(msg, flush=True)


# ─────────────────────────────────────────────────────────── pure helpers (tested)
def _rel(a, b):
    return abs(a - b) / max(abs(b), 1e-9)


def weekday_sessions(after, upto, holidays):
    """ISO dates in (after, upto] that are weekdays and not NSE holidays."""
    out, d = [], date.fromisoformat(after) + timedelta(days=1)
    end = date.fromisoformat(upto)
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def last_completed_session(holidays, today):
    """Latest weekday non-holiday strictly BEFORE today (IST). Today's candle is never
    taken by default: Angel's same-evening bars were seen carrying provisional closes."""
    d = today - timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d -= timedelta(days=1)
    return d.isoformat()


def classify_overlap(stored, angel):
    """stored: [(date,o,h,l,c,v)] oldest->newest (the symbol's last stored bars).
    angel: {date: (o,h,l,c,v)}.
    Returns (ok: bool, category, reason, ratio)."""
    if len(stored) < MIN_OVERLAP:
        return False, "overlap_insufficient", f"only {len(stored)} stored bars to compare (need {MIN_OVERLAP})", None
    missing = [s[0] for s in stored if s[0] not in angel]
    if missing:
        return False, "overlap_missing", f"Angel returned no bar for stored day(s) {missing}", None
    rows = []
    for d, o, h, l, c, v in stored:
        a = angel[d]
        bad = [f for f, x, y in zip("OHLC", a[:4], (o, h, l, c)) if y is None or _rel(x, y) > REL_TOL]
        vbad = False if v is None else (a[4] != v) if not v else _rel(a[4], v) > REL_TOL
        rows.append((d, bad, vbad, a[3] / c if c else float("nan")))
    if not any(r[1] or r[2] for r in rows):
        return True, "exact", "overlap exact", 1.0
    ratios = [r[3] for r in rows]
    med = statistics.median(ratios)
    if all(r[1] for r in rows) and max(ratios) / min(ratios) - 1 < RATIO_SPREAD and abs(med - 1) > RATIO_SPREAD:
        return (False, "corporate_action",
                f"consistent ratio {med:.5f} on all {len(rows)} overlap days: Angel back-adjusted for a "
                f"corporate action after these rows were stored", round(med, 5))
    bad_days = [r[0] for r in rows if r[1] or r[2]]
    if bad_days == [stored[-1][0]] and abs(rows[-1][3] - 1) <= PROVISIONAL_MAX:
        return (True, "provisional_last_bar",
                f"overlap exact except the last stored bar {stored[-1][0]} (close ratio {rows[-1][3]:.4f}, "
                f"fields {rows[-1][1] or '-'}, volume differs={rows[-1][2]}); stored value was provisional", 1.0)
    detail = [(r[0], r[1], round(r[3], 4)) for r in rows if r[1] or r[2]]
    return False, "overlap_mismatch", f"overlap disagrees without one consistent ratio: {detail}", None


def market_sessions(windows, fetched, target, holidays, muhurat):
    """Decide the session calendar from the holiday list + cross-section.
    windows: {sym: last_stored_date}; fetched: {sym: set(dates)} (bars after last_stored).
    Returns (sessions:set, special:list, unannounced:list, notes:list)."""
    lo = min(windows.values())
    base = set(weekday_sessions(lo, target, holidays))
    active = {s for s in windows if fetched.get(s)}
    cover = collections.Counter(d for s in active for d in fetched[s])

    def denom(d):
        return sum(1 for s in active if windows[s] < d)

    special, unannounced, notes = [], [], []
    for d in sorted(set(cover) - base):
        n = denom(d)
        if n >= MIN_DENOM and cover[d] / n >= SPECIAL_MIN_COVER:
            special.append(d)
            notes.append(f"special session {d} ({cover[d]}/{n} symbols"
                         f"{', listed Muhurat' if d in muhurat else ', NOT in NSE list'})")
    for d in sorted(base):
        n = denom(d)
        if n >= MIN_DENOM and cover[d] / n < HOLIDAY_MAX_COVER:
            unannounced.append(d)
            notes.append(f"weekday {d} has bars for only {cover[d]}/{n} symbols: treated as an unannounced holiday")
    return (base - set(unannounced)) | set(special), special, unannounced, notes


def validate_bars(bars, expected, is_index):
    """bars: [(date,o,h,l,c,v)] after the last stored date. Returns list of problems."""
    probs = []
    ds = [b[0] for b in bars]
    if len(ds) != len(set(ds)):
        probs.append("duplicate dates")
    missing = sorted(set(expected) - set(ds))
    extra = sorted(set(ds) - set(expected))
    if missing:
        probs.append(f"missing {len(missing)} session(s) {missing[:5]}{'...' if len(missing) > 5 else ''}")
    if extra:
        probs.append(f"bars on non-session date(s) {extra[:5]}")
    for d, o, h, l, c, v in bars:
        if min(o, h, l, c) <= 0:
            probs.append(f"zero/negative price on {d}")
        if h < max(o, c) - 1e-6 or l > min(o, c) + 1e-6:
            probs.append(f"high/low inconsistent on {d}")
        if not is_index and v == 0 and o == h == l == c:
            probs.append(f"flat zero-volume filler bar on {d}")
    return probs


def spot_dates(pool, target, k=2):
    """Official-price check dates: the target plus k dates spread evenly over the pool."""
    pool = sorted(d for d in pool if d != target)
    picks = pool if len(pool) <= k else [pool[int((i + 0.5) * len(pool) / k)] for i in range(k)]
    return sorted(set([target] + picks))


def official_row(bhav_day, name):
    """The symbol's row in one day's bhavcopy (EQ, else BE, else any series), or None."""
    rows = (bhav_day or {}).get(name)
    if not rows:
        return None
    return rows.get("EQ") or rows.get("BE") or next(iter(rows.values()))


def bhav_check(angel, official):
    """angel: {date: (o,h,l,c,v)}. official: {date: bhavcopy row or None} for the loaded
    bhavcopy dates inside the symbol's window. Returns dict of date lists:
    matched, differ (as text), absent (Angel bar, no NSE row), nse_only (NSE row, no Angel bar)."""
    res = {"matched": [], "differ": [], "absent": [], "nse_only": []}
    for d in sorted(official):
        row, bar = official[d], angel.get(d)
        if bar is None and row is None:
            continue
        if bar is None:
            res["nse_only"].append(d)
            continue
        if row is None:
            res["absent"].append(d)
            continue
        off = [float(row["OpnPric"]), float(row["HghPric"]), float(row["LwPric"]), float(row["ClsPric"])]
        vol = int(float(row["TtlTradgVol"]))
        if all(abs(x - y) <= 0.011 for x, y in zip(bar[:4], off)) and int(bar[4]) == vol:
            res["matched"].append(d)
        else:
            res["differ"].append(f"{d}: angel C={bar[3]} V={bar[4]} vs official C={off[3]} V={vol}")
    return res


def seed_exclusions(candidates, overrides, hold_back, isins):
    """Symbols that must never be seeded, with the reason.
    - hold_back entries (corporate-action seams)
    - aliases: the NSE/Angel name of a symbol already stored under another name
      (seeding it would start a second, duplicate series, e.g. HEGAM for stored HEG)
    - NSE placeholder constituents for a pending demerger (DUMMY* symbol / DUM* ISIN)."""
    alias_of = {}
    for stored, o in overrides.items():
        for name in (o.get("nse_symbol"), (o.get("angel_symbol") or "").rsplit("-", 1)[0]):
            if name and name != stored:
                alias_of[name] = stored
    out = {}
    for s in candidates:
        if s in hold_back:
            out[s] = ("held_back", "listed in symbol_overrides.json hold_back")
        elif s in alias_of:
            out[s] = ("alias", f"NSE/Angel name of {alias_of[s]}, which is already stored under that name "
                               f"(symbol_overrides.json); seeding would start a duplicate series")
        elif s.startswith("DUMMY") or (isins.get(s) or "").upper().startswith("DUM"):
            out[s] = ("placeholder", f"NSE dummy constituent (ISIN {isins.get(s) or '?'}) standing in for a "
                                     f"demerged entity until it lists; not a traded security")
    return out


def seed_decision(angel, official, sessions, seed_from, target, acts):
    """Decide one seed (a symbol with NO stored rows). There is nothing stored to overlap,
    so identity and basis are proven against the NSE bhavcopy instead.
    angel: {date: (o,h,l,c,v)} as fetched; official: {date: row|None} for the loaded
    bhavcopy dates in [seed_from, target]; acts: [(ex_date, subject)] matching CA_PATTERN.
    Returns (ok, category, reason, info)."""
    info = {}
    in_window = sorted((ex, subj) for ex, subj in acts if seed_from <= ex <= target)
    if in_window:
        return (False, "corporate_action_in_window",
                f"{in_window} inside the seed window: bars before the ex-date are on a different basis "
                f"(raw or back-adjusted). Seed it with --symbols <SYM> --seed-from <first session after "
                f"{in_window[-1][0]}>", info)
    bars = sorted((d,) + tuple(angel[d]) for d in angel if seed_from <= d <= target)
    if not bars:
        return False, "no_bars", f"Angel returned no bars between {seed_from} and {target}", info
    first = bars[0][0]
    info.update(first=first, sessions=len(bars))
    probs = validate_bars(bars, sorted(d for d in sessions if first <= d <= target), False)
    if probs:
        return False, "validation", "; ".join(probs), info
    res = bhav_check({b[0]: b[1:] for b in bars}, official)
    info["bhav"] = res
    if res["nse_only"]:
        return (False, "seed_incomplete", f"NSE bhavcopy has it on {res['nse_only']} but Angel has no bar there "
                                          f"(Angel's first bar is {first})", info)
    if res["differ"] or res["absent"]:
        return (False, "official_price_mismatch",
                "; ".join(res["differ"] + [f"{d}: Angel bar but no NSE bhavcopy row" for d in res["absent"]]), info)
    need = min(SEED_MIN_BHAV, len([d for d in official if first <= d <= target]))
    if need == 0 or len(res["matched"]) < need:
        return (False, "unverified", f"{len(res['matched'])} official-price match(es) on {res['matched']}; need "
                                     f"{max(need, 1)}. Is the NSE bhavcopy reachable?", info)
    first_session = min((d for d in sessions if d >= seed_from), default=first)
    info["listed_in_window"] = first > first_session
    return True, "seed", f"{len(bars)} sessions from {first}; official price matched on {res['matched']}", info


# ─────────────────────────────────────────────────────────────────── NSE (public)
def nse_session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com", timeout=10)
    except Exception:
        pass
    return s


def load_holidays(sess, notes):
    bundled = json.loads((HERE / "nse_holidays.json").read_text())["holidays"]
    by_year = {y: {r["date"]: r["description"] for r in rows} for y, rows in bundled.items()}
    try:
        r = sess.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=20)
        r.raise_for_status()
        live = {}
        for x in r.json().get("CM", []):
            iso = datetime.strptime(x["tradingDate"], "%d-%b-%Y").date().isoformat()
            live.setdefault(iso[:4], {})[iso] = x["description"].strip()
        for y, rows in live.items():
            if y in by_year and by_year[y] != rows:
                notes.append(f"NSE live holiday list for {y} differs from the bundled copy: "
                             f"added {sorted(set(rows) - set(by_year[y]))}, removed {sorted(set(by_year[y]) - set(rows))}; using live")
            by_year[y] = rows
        notes.append(f"holiday list: live NSE for {sorted(live)}, bundled for {sorted(set(by_year) - set(live))}")
    except Exception as e:
        notes.append(f"WARNING: live NSE holiday list unavailable ({e}); using bundled years {sorted(by_year)}")
    hol = {d: desc for rows in by_year.values() for d, desc in rows.items()}
    muh = {d for d, desc in hol.items() if desc.endswith("*")}
    return hol, muh, set(by_year)


def fetch_corporate_actions(sess, d0, d1, notes):
    out, a = [], date.fromisoformat(d0)
    end = date.fromisoformat(d1)
    while a <= end:
        b = min(a + timedelta(days=89), end)
        url = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
               f"&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}")
        try:
            r = sess.get(url, timeout=30)
            r.raise_for_status()
            out.extend(r.json())
        except Exception as e:
            notes.append(f"WARNING: NSE corporate-action feed failed for {a}..{b} ({e}); "
                         "the overlap-ratio check still runs but in-window actions Angel has NOT adjusted can slip through")
            return None
        a = b + timedelta(days=1)
        time.sleep(0.5)
    return out


def fetch_bhavcopy(sess, iso):
    d = date.fromisoformat(iso)
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    for _ in range(3):
        try:
            r = sess.get(url, timeout=30)
        except Exception:
            time.sleep(1.5)
            continue
        if r.status_code == 200 and r.content[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(r.content))
            text = z.read(next(n for n in z.namelist() if n.lower().endswith(".csv"))).decode("utf-8", "ignore")
            out = {}
            for row in csv.DictReader(io.StringIO(text)):
                row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                if row.get("FinInstrmTp") == "STK":
                    out.setdefault(row["TckrSymb"], {})[row["SctySrs"]] = row
            return out
        if r.status_code == 404:
            return None
        time.sleep(1.0)
    return None


# ─────────────────────────────────────────────────────────────────────── Turso
def turso():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from quant_engine.data.turso_client import connect
    return connect()


def q(conn, sql, params=()):
    return conn.execute(sql, list(params)).fetchall()


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def last_bars(conn, symbols):
    """{symbol: MAX(date) or None}. One pipelined point query per symbol, each served by the
    UNIQUE(symbol, date) index, so a few hundred row-reads instead of a full-table GROUP BY."""
    from quant_engine.data.turso_client import _build_stmt
    out = {}
    for part in chunks(sorted(symbols), 400):
        reqs = [{"type": "execute", "stmt": _build_stmt("SELECT MAX(date) FROM price_history WHERE symbol = ?", [s])}
                for s in part] + [{"type": "close"}]
        data = conn.cursor()._post({"requests": reqs})
        for s, res in zip(part, data["results"]):
            if res.get("type") == "error":
                raise SystemExit(f"STOP: read failed for {s}: {res.get('error')}")
            rows = res["response"]["result"]["rows"]
            v = rows[0][0] if rows else None
            out[s] = v.get("value") if isinstance(v, dict) else v
    return out


def post_inserts(conn, rows):
    """One pipeline request of INSERT OR IGNOREs. Returns (inserted, [errors])."""
    from quant_engine.data.turso_client import _build_stmt
    sql = ("INSERT OR IGNORE INTO price_history (symbol, date, open, high, low, close, volume) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)")
    reqs = [{"type": "execute", "stmt": _build_stmt(sql, r)} for r in rows] + [{"type": "close"}]
    data = conn.cursor()._post({"requests": reqs})
    ins, errs = 0, []
    for res in data.get("results", [])[:len(rows)]:
        if res.get("type") == "error":
            errs.append(res.get("error", {}).get("message", str(res)))
        else:
            ins += int(res["response"]["result"].get("affected_row_count", 0))
    return ins, errs


def write_batch(conn, rows):
    """Write with retry on transient failures only. Raises SystemExit on anything that
    looks like a quota/limit/permission response or any statement error."""
    for attempt in range(1, 4):
        try:
            ins, errs = post_inserts(conn, rows)
        except Exception as e:
            resp = getattr(e, "response", None)
            body = (getattr(resp, "text", "") or "")[:400]
            status = getattr(resp, "status_code", None)
            text = f"{e} {body}"
            if (status and 400 <= status < 500) or any(w in text.lower() for w in STOP_WORDS):
                raise SystemExit(f"STOP: Turso refused the write (HTTP {status}): {text}")
            if attempt == 3:
                raise SystemExit(f"STOP: Turso write failed 3 times: {text}")
            log(f"  transient write error, retrying ({text[:120]})")
            time.sleep(2 ** attempt)
            continue
        if errs:
            raise SystemExit(f"STOP: statement error(s) from Turso: {errs[:3]}")
        return ins


def readback(conn, rows):
    """Compare stored rows with what we meant to write. Returns list of mismatches."""
    want = {(r[0], r[1]): r for r in rows}
    got = {}
    by_sym = collections.defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r[1])
    for syms in chunks(sorted(by_sym), 200):
        lo = min(min(by_sym[s]) for s in syms)
        hi = max(max(by_sym[s]) for s in syms)
        for r in q(conn, f"SELECT symbol,date,open,high,low,close,volume FROM price_history "
                         f"WHERE date BETWEEN ? AND ? AND symbol IN ({','.join('?' * len(syms))})",
                   [lo, hi] + syms):
            if (r[0], r[1]) in want:
                got[(r[0], r[1])] = tuple(r)
    return [(k, want[k], got.get(k)) for k in want if got.get(k) != tuple(want[k])]


# ───────────────────────────────────────────────────────────────────────── main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", help="last session to fill (default: last completed NSE session before today, IST)")
    ap.add_argument("--apply", action="store_true", help="WRITE to Turso. Without it nothing is written.")
    ap.add_argument("--symbols", help="comma list: restrict to these stored symbols (testing / re-runs)")
    ap.add_argument("--allow-today", action="store_true",
                    help="permit --to = today (IST). Same-evening Angel candles can be provisional.")
    ap.add_argument("--allow-missing-sessions", action="store_true",
                    help="write symbols even if some expected sessions are absent (e.g. a suspension)")
    ap.add_argument("--no-bhavcopy", action="store_true", help="skip the NSE official-price spot check")
    ap.add_argument("--allow-no-ca-feed", action="store_true",
                    help="write even if the NSE corporate-action feed could not be read (unsafe: an "
                         "unadjusted split inside the window would then go undetected)")
    ap.add_argument("--universe-file", default=str(ROOT / "catalan/data/universe/nifty500_2026-10-18.csv"),
                    help="extra symbols to keep current (NSE constituent CSV; EQ rows). Default: the "
                         "CATALAN v2 frozen universe, if present")
    ap.add_argument("--seed", action="store_true",
                    help="SEED mode: universe-file symbols with NO price_history rows get bars from "
                         "--seed-from to the target (no overlap check; proven against the NSE bhavcopy)")
    ap.add_argument("--seed-from", default=SEED_FROM, help=f"first date to seed (default {SEED_FROM})")
    ap.add_argument("--out-dir", default=str(ROOT / "data/archive/price_gap_backfill"),
                    help="where run artefacts go (default is gitignored)")
    a = ap.parse_args(argv)
    if a.seed and a.no_bhavcopy:
        raise SystemExit("--seed needs the NSE bhavcopy check: it is the only verification a seed has")

    today = datetime.now(IST).date()
    run_dir = Path(a.out_dir) / datetime.now(IST).strftime("%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    sess = nse_session()
    holidays, muhurat, hol_years = load_holidays(sess, notes)

    target = a.to or last_completed_session(holidays, today)
    if target > today.isoformat():
        raise SystemExit(f"--to {target} is in the future")
    if target == today.isoformat() and not a.allow_today:
        raise SystemExit("--to is today: pass --allow-today if you really want a same-day candle (see README)")
    mapping = json.loads((HERE / "symbol_overrides.json").read_text())
    overrides, hold_back = mapping["overrides"], mapping.get("hold_back", {})
    nse_name = {s: o.get("nse_symbol", s) for s, o in overrides.items()}

    log(f"== price_history gap backfill  [{'SEED' if a.seed else 'EXTEND'} mode]  "
        f"[{'APPLY' if a.apply else 'DRY RUN, nothing will be written'}]")
    log(f"   target session: {target}   run dir: {run_dir}")
    if a.seed and a.seed_from > target:
        raise SystemExit(f"--seed-from {a.seed_from} is after the target {target}")

    # ── scope (reads only) ─────────────────────────────────────────────────────
    conn = turso()
    gmax = q(conn, "SELECT MAX(date) FROM price_history")[0][0]
    recent = [r[0] for r in q(conn, "SELECT date FROM price_history WHERE date > date(?, '-60 day') "
                                     "GROUP BY date HAVING COUNT(*) >= 50 ORDER BY date DESC LIMIT ?",
                              [gmax, SCOPE_SESSIONS])]
    active = {r[0] for r in q(conn, "SELECT DISTINCT symbol FROM price_history WHERE date >= ?", [recent[-1]])}
    from quant_engine.data.membership import MembershipRegistry
    reg = MembershipRegistry.from_turso(conn, "NIFTY500")
    n500 = set(reg.members_on(date.fromisoformat(target), "NIFTY500"))
    uni_path = Path(a.universe_file) if a.universe_file else None
    uni, uni_isin = set(), {}
    if uni_path and uni_path.exists():
        import csv as _csv
        for r in _csv.DictReader(uni_path.read_text().splitlines()):
            if r.get("Series", "").strip() == "EQ":
                sym = r["Symbol"].strip().upper()
                uni.add(sym)
                uni_isin[sym] = (r.get("ISIN Code") or r.get("ISIN") or "").strip()
        notes.append(f"universe file {uni_path.name}: {len(uni)} EQ symbols added to the scope")
        n500 |= uni
    elif a.seed:
        raise SystemExit(f"--seed needs the universe file, not found: {uni_path}")
    want = {s.strip().upper() for s in a.symbols.split(",") if s.strip()} if a.symbols else set()
    found = last_bars(conn, active | n500 | want)
    lastbar = {s: L for s, L in found.items() if L}
    n500_nodata = sorted(s for s in n500 if not found.get(s))
    log(f"   table MAX(date) {gmax}; recent-{SCOPE_SESSIONS}-session window {recent[-1]}..{recent[0]}")
    pre_skips, current = {}, []
    if a.seed:
        # Seeds: universe symbols with NO stored rows. `after` is the day before the seed
        # window, so every date in [seed_from, target] counts as new.
        cands = sorted(s for s in uni if not found.get(s))
        if a.symbols:
            have_rows = sorted(want & set(lastbar))
            if have_rows:
                notes.append(f"--symbols already stored, so not seeds (use extend mode): {have_rows}")
            cands = [s for s in cands if s in want]
        pre_skips = seed_exclusions(cands, overrides, hold_back, uni_isin)
        todo = [s for s in cands if s not in pre_skips]
        day_before = (date.fromisoformat(a.seed_from) - timedelta(days=1)).isoformat()
        after = {s: day_before for s in todo}
        log(f"   seed candidates: {len(cands)} universe symbols with no price_history; excluded by rule "
            f"{len(pre_skips)}; to seed {len(todo)} from {a.seed_from}")
    else:
        scope = active | (n500 & set(lastbar))
        if a.symbols:
            unknown = sorted(want - set(lastbar))
            if unknown:
                notes.append(f"--symbols not in price_history (ignored; use --seed for unseeded symbols): {unknown}")
            scope = want & set(lastbar)
        current = sorted(s for s in scope if lastbar[s] >= target)
        todo = sorted(s for s in scope if lastbar[s] < target)
        after = {s: lastbar[s] for s in todo}
        log(f"   scope {len(scope)} symbols " + (f"(--symbols)" if a.symbols else
            f"({len(active)} recently active, plus {len((n500 & set(lastbar)) - active)} other current NIFTY500 members)") + "; "
            f"already at target {len(current)}; to fill {len(todo)}")
        if n500_nodata:
            log(f"   NIFTY500/universe symbols with NO price_history at all (use --seed): {n500_nodata}")
    if not todo:
        for s in sorted(pre_skips):
            log(f"     {s:<12} [{pre_skips[s][0]}] {pre_skips[s][1]}")
        log("Nothing to do.")
        return 0

    # last N stored bars per symbol (extend mode only; a seed has nothing stored)
    stored = collections.defaultdict(list)
    by_L = collections.defaultdict(list)
    for s in ([] if a.seed else todo):
        by_L[lastbar[s]].append(s)
    for L, syms in by_L.items():
        for part in chunks(syms, 300):
            for r in q(conn, f"SELECT symbol,date,open,high,low,close,volume FROM price_history "
                             f"WHERE date BETWEEN date(?, '-30 day') AND ? AND symbol IN ({','.join('?' * len(part))}) "
                             f"ORDER BY symbol, date", [L, L] + part):
                stored[r[0]].append(tuple(r[1:]))
    for s in todo:
        stored[s] = stored[s][-N_OVERLAP:]

    # ── fetch (Angel, read-only) ──────────────────────────────────────────────
    reqs = []
    for s in todo:
        first = a.seed_from if a.seed else (stored[s][0][0] if stored[s] else lastbar[s])
        start = (date.fromisoformat(first) - timedelta(days=FETCH_LEAD_DAYS)).isoformat()
        reqs.append({"symbol": s, "from": start, "to": target})
    job, out = run_dir / "angel_job.json", run_dir / "angel_candles.json"
    job.write_text(json.dumps({"requests": reqs, "overrides": overrides, "gap_ms": GAP_MS}))
    log(f"-- fetching {len(reqs)} symbols from Angel (~{len(reqs) * GAP_MS / 60000:.1f} min at 2.5 req/s)")
    rc = subprocess.run(["node", str(HERE / "angel_fetch.mjs"), str(job), str(out)], cwd=str(ROOT)).returncode
    if rc not in (0,) or not out.exists():
        raise SystemExit(f"STOP: Angel fetch failed (exit {rc}); nothing written")
    F = json.loads(out.read_text())
    A = {s: {r[0]: tuple(r[1:]) for r in rows} for s, rows in F["data"].items()}

    # ── market calendar ───────────────────────────────────────────────────────
    lo_year, hi_year = min(after[s] for s in todo)[:4], target[:4]
    for y in range(int(lo_year), int(hi_year) + 1):
        if str(y) not in hol_years:
            notes.append(f"WARNING: no NSE holiday list for {y}; sessions for {y} inferred from the cross-section only")
    windows = {s: after[s] for s in todo if s in A}
    fill_dates = {s: {d for d in A[s] if after[s] < d <= target} for s in windows}
    sessions, special, unannounced, cal_notes = market_sessions(windows, fill_dates, target, holidays, muhurat)
    notes += cal_notes

    # ── corporate actions + official-price spot check ─────────────────────────
    ca_rows = fetch_corporate_actions(sess, min(windows.values()) if windows else target, target, notes)
    if ca_rows is None and a.apply and not a.allow_no_ca_feed:
        # Verifier C1: without the feed, a split/bonus inside the window that
        # Angel has not back-adjusted yet passes the overlap check (the stored
        # overlap still matches) and raw post-ex-date bars would be written:
        # the TRENT/CUB/HEG seam again, this time inside counted sessions.
        raise SystemExit("STOP: the NSE corporate-action feed could not be read, so in-window "
                         "splits/bonuses cannot be ruled out. Nothing written. Retry later, or pass "
                         "--allow-no-ca-feed if you accept that risk.")
    ca = collections.defaultdict(list)
    back = {v: k for k, v in nse_name.items()}
    for r in ca_rows or []:
        try:
            ex = datetime.strptime(r["exDate"], "%d-%b-%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        sym = back.get(r.get("symbol"), r.get("symbol"))
        ca[sym].append((ex, r.get("subject", "").strip()))

    all_fill = sorted({d for ds in fill_dates.values() for d in ds})
    bhav_dates = []
    if all_fill and not a.no_bhavcopy:
        cover = collections.Counter(d for ds in fill_dates.values() for d in ds)
        pool = [d for d in all_fill if cover[d] >= 0.5 * len(fill_dates)]
        bhav_dates = spot_dates(pool, target)
    bhav = {}
    for d in bhav_dates:
        b = fetch_bhavcopy(sess, d)
        if b is None:
            notes.append(f"WARNING: NSE bhavcopy for {d} unavailable; official-price check skipped for that date")
        else:
            bhav[d] = b

    # ── per-symbol decision ───────────────────────────────────────────────────
    plan, skips, provisional, big, bhav_stats = {}, dict(pre_skips), [], [], collections.Counter()
    seed_info = {}
    for s in todo:
        L = after[s]
        if s in F.get("unresolved", {}):
            u = F["unresolved"][s]
            skips[s] = ("unresolved", f"{u.get('error')}; candidates: {u.get('candidates') or 'none'} "
                                      f"-> add to symbol_overrides.json if one is the same security")
            continue
        if s in F.get("errors", {}):
            skips[s] = ("fetch_error", F["errors"][s])
            continue
        if s not in A:
            skips[s] = ("fetch_error", "no response recorded")
            continue
        acts = [(ex, subj) for ex, subj in ca.get(s, []) if CA_PATTERN.search(subj)]
        if a.seed:
            official = {d: official_row(bhav[d], nse_name.get(s, s)) for d in bhav if a.seed_from <= d <= target}
            ok, cat, why, info = seed_decision(A[s], official, sessions, a.seed_from, target, acts)
            res = info.get("bhav") or {}
            bhav_stats["match"] += len(res.get("matched", []))
            bhav_stats["differ"] += len(res.get("differ", [])) + len(res.get("absent", []))
            bhav_stats["nse_only"] += len(res.get("nse_only", []))
            if not ok:
                skips[s] = (cat, why)
                continue
            bars = sorted((d,) + A[s][d] for d in A[s] if a.seed_from <= d <= target)
            prev = None
            for b in bars:
                if prev and abs(b[4] / prev - 1) > BIG_MOVE:
                    big.append((s, b[0], prev, b[4], round((b[4] / prev - 1) * 100, 1)))
                prev = b[4]
            plan[s] = [(s, d, float(o), float(h), float(l), float(c), int(v)) for d, o, h, l, c, v in bars]
            seed_info[s] = {k: v for k, v in info.items() if k != "bhav"}
            seed_info[s]["matched"] = res.get("matched", [])
            continue
        ok, cat, why, ratio = classify_overlap(stored[s], A[s])
        if s in hold_back:
            h = hold_back[s]
            skips[s] = ("held_back", f"listed in symbol_overrides.json hold_back ({h.get('action')}, stored through "
                                     f"{h.get('stored_through')}); overlap now: {cat}"
                                     f"{f' ratio {ratio}' if ratio not in (None, 1.0) else ''}. Never extended by this "
                                     f"tool until its history is rewritten on one basis")
            continue
        explain = [(ex, subj) for ex, subj in ca.get(s, []) if CA_PATTERN.search(subj) or "right" in subj.lower()]
        if not ok:
            if cat == "corporate_action" and explain:
                why += f"; NSE feed: {explain}"
            elif cat == "corporate_action":
                why += "; no matching action found in the NSE feed for this window, so check manually"
            skips[s] = (cat, why)
            continue
        in_window = [(ex, subj) for ex, subj in acts if L < ex <= target]
        if in_window:
            skips[s] = ("corporate_action_in_window",
                        f"{in_window}: ex-date falls inside the fill window; Angel has not back-adjusted the stored "
                        f"days yet, so writing would put an unadjusted jump at the ex-date")
            continue
        exp = sorted(d for d in sessions if L < d <= target)
        bars = sorted((d,) + A[s][d] for d in A[s] if L < d <= target)
        probs = validate_bars(bars, exp, s.startswith("^"))
        if probs and a.allow_missing_sessions:
            probs = [p for p in probs if not p.startswith("missing")]
        if not bars and not exp:
            current.append(s)      # no session between its last bar and the target
            continue
        if not bars:
            skips[s] = ("no_new_bars", f"Angel returned no bars after {L} (suspended or delisted?)")
            continue
        if probs:
            skips[s] = ("validation", "; ".join(probs))
            continue
        if not s.startswith("^"):
            res = bhav_check({d: A[s][d] for d in A[s] if L < d <= target},
                             {d: official_row(b, nse_name.get(s, s)) for d, b in bhav.items() if L < d <= target})
            bhav_stats["match"] += len(res["matched"])
            bhav_stats["differ"] += len(res["differ"])
            bhav_stats["absent"] += len(res["absent"])
            if res["differ"]:
                skips[s] = ("official_price_mismatch", "; ".join(res["differ"]) + " (provisional candle or adjustment)")
                continue
        prev = stored[s][-1][4]
        for b in bars:
            if prev and abs(b[4] / prev - 1) > BIG_MOVE:
                big.append((s, b[0], prev, b[4], round((b[4] / prev - 1) * 100, 1)))
            prev = b[4]
        plan[s] = [(s, d, float(o), float(h), float(l), float(c), int(v)) for d, o, h, l, c, v in bars]
        if cat == "provisional_last_bar":
            provisional.append((s, why))

    rows = [r for s in sorted(plan) for r in plan[s]]
    per_date = collections.Counter(r[1] for r in rows)

    # PIT NIFTY500 coverage after the (planned) fill, on a sample of fill dates
    cov_dates = sorted(per_date)
    if len(cov_dates) > 12:
        k = len(cov_dates) / 12
        cov_dates = sorted({cov_dates[int(i * k)] for i in range(12)} | {cov_dates[-1]})
    have = collections.defaultdict(set)
    if cov_dates:
        for d, s in q(conn, f"SELECT date, symbol FROM price_history WHERE date IN ({','.join('?' * len(cov_dates))})",
                      cov_dates):
            have[d].add(s)
    for r in rows:
        have[r[1]].add(r[0])

    # ── report ────────────────────────────────────────────────────────────────
    log("\n== calendar")
    log(f"   sessions in window: {len([d for d in sessions if d > min(windows.values(), default=target)])}; "
        f"special sessions: {special or 'none'}; unannounced holidays: {unannounced or 'none'}")
    for n in notes:
        log(f"   - {n}")
    log(f"\n== decisions: write {len(plan)} symbols / {len(rows)} rows;  skipped {len(skips)};  already current {len(current)}")
    if skips:
        log("   SKIPPED (nothing written for these):")
        for s in sorted(skips):
            log(f"     {s:<12} [{skips[s][0]}] {skips[s][1]}")
    if provisional:
        log(f"   provisional last stored bar (allowed, not corrected): {[p[0] for p in provisional]}")
    if seed_info:
        log("   SEEDS (would be written):  symbol  first bar  sessions  official-price matches")
        for s in sorted(seed_info):
            i = seed_info[s]
            flags = (" listed inside the window" if i.get("listed_in_window") else "") + \
                    (f" SHORT (<{SEED_SHORT} sessions)" if i["sessions"] < SEED_SHORT else "")
            log(f"     {s:<12} {i['first']}  {i['sessions']:>4}   {i['matched']}{flags}")
    if bhav:
        log(f"   official-price spot check {sorted(bhav)}: {dict(bhav_stats)}")
    if big:
        log(f"   daily moves > {int(BIG_MOVE * 100)}% (review; circuits/news are normal): {big}")
    log("\n== rows per date (planned)   [index_membership NIFTY500 with a bar after fill / members]"
        + ("   [universe file with a bar after fill / size]" if uni else ""))
    for d in sorted(per_date):
        cov = ""
        if d in cov_dates:
            m = reg.members_on(date.fromisoformat(d), "NIFTY500")
            cov = f"   [{len(m & have[d])}/{len(m)}]" + (f"   [{len(uni & have[d])}/{len(uni)}]" if uni else "")
        log(f"   {d}  {per_date[d]:>4}{cov}")
    report = {"mode": "seed" if a.seed else "extend", "seed_from": a.seed_from if a.seed else None,
              "seed_info": seed_info, "bhavcopy_dates": sorted(bhav),
              "target": target, "apply": a.apply, "notes": notes, "special_sessions": special,
              "unannounced_holidays": unannounced, "skips": skips, "provisional": provisional, "big_moves": big,
              "bhavcopy": dict(bhav_stats), "rows_per_date": per_date, "n500_no_data": n500_nodata,
              "plan": {s: v for s, v in plan.items()}}
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    log(f"\n   report: {run_dir / 'report.json'}")

    if not a.apply:
        log("\nDRY RUN: nothing written. Re-run with --apply to write the rows above.")
        return 0
    if not rows:
        log("Nothing to write.")
        return 0

    # ── write: INSERT OR IGNORE, first small batch, then the rest ─────────────
    wsyms = sorted(plan)
    existing = set()
    for part in chunks(wsyms, 300):
        lo = min(after[s] for s in part)
        existing |= {(r[0], r[1]) for r in q(conn, f"SELECT symbol, date FROM price_history WHERE date > ? AND "
                                                   f"symbol IN ({','.join('?' * len(part))})", [lo] + part)}
    if a.seed:
        # A seed must still have NO rows at write time; if another writer got there first,
        # the symbol now needs the extend mode's overlap check instead.
        gained = sorted({k[0] for k in existing}) + sorted(s for s, L in last_bars(conn, set(wsyms)).items() if L)
        gained = sorted(set(gained))
        if gained:
            log(f"   seeds that gained rows since the fetch, dropped (run extend mode for them): {gained}")
            rows = [r for r in rows if r[0] not in gained]
            wsyms = [s for s in wsyms if s not in gained]
            if not rows:
                log("Nothing to write.")
                return 0
    pending = [r for r in rows if (r[0], r[1]) not in existing]
    if len(pending) != len(rows):
        log(f"   {len(rows) - len(pending)} planned rows already exist (another writer?); left untouched")
    first = [r for r in pending if r[0] == pending[0][0]]
    log(f"\n== writing: first batch = {first[0][0]} ({len(first)} rows)")
    ins = write_batch(conn, first)
    mism = readback(conn, first)
    if ins != len(first) or mism:
        raise SystemExit(f"STOP after first batch: inserted {ins}/{len(first)}, readback mismatches {mism[:3]}")
    log("   first batch OK (inserted and read back identical)")
    total = ins
    rest = [r for r in pending if r[0] != first[0][0]]
    for i, part in enumerate(chunks(rest, BATCH), 1):
        n = write_batch(conn, part)
        total += n
        log(f"   batch {i}: sent {len(part)}, inserted {n}")
        time.sleep(0.3)
    mism = readback(conn, pending)
    log(f"\n== wrote {total} rows for {len(wsyms)} symbols; readback mismatches: {len(mism)}")
    for m in mism[:10]:
        log(f"   {m}")
    log(f"   MAX(date) now {q(conn, 'SELECT MAX(date) FROM price_history')[0][0]}")
    (run_dir / "write_result.json").write_text(json.dumps({"inserted": total, "readback_mismatches": mism}, default=str))
    return 4 if mism else 0


if __name__ == "__main__":
    sys.exit(main())
