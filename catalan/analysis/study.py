"""
catalan-v2 analysis — PREREGISTRATION.md Amendment 1, implemented literally.

Every function below corresponds to a numbered definition in Amendment 1 and
takes plain DataFrames, so each rule is unit-tested on synthetic data
(tests/test_study.py). Nothing here reads a return before it is allowed to:

  * ``--pilot`` computes on the pilot window only (announcements dated before
    STUDY_CLOCK_START). Allowed any time after the amendment was committed,
    for pipeline validation; nothing learned may change a v2 parameter.
  * ``--gate C0`` refuses until 40 usable counted sessions with both baskets
    non-empty exist, then evaluates ONCE and writes forward_log/results/C0.json.
  * ``--gate C1C2`` refuses until 120 usable counted sessions exist, then
    evaluates ONCE over the first 120 and writes forward_log/results/C1C2.json.

A written result is never recomputed: the stopping rule, enforced in code.

Run:
    python3 -m catalan.analysis.study --pilot --since 2026-10-01 --until 2026-10-23
    python3 -m catalan.analysis.study --gate C0
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

from catalan.config import (
    NEWS_CUTOFF,
    BROKERAGE_PER_ORDER_CAP,
    BROKERAGE_RATE,
    EXCHANGE_TXN_RATE,
    GST_RATE,
    HEDGE_COST_BPS,
    POSITION_NOTIONAL_INR,
    PRIMARY_MODEL,
    SEBI_TURNOVER_RATE,
    SPREAD_MIN_PAIRS,
    SPREAD_WINDOW_SESSIONS,
    STAMP_DUTY_BUY_RATE,
    STT_SELL_RATE,
    STUDY_CLOCK_START,
    TEMPERATURE,
    USABLE_SESSION_MIN_COVERAGE,
)
from catalan.data.archive import ARCHIVE_ROOT
from catalan.data.panel import OVERNIGHT, attribution_dates, classify_session
from catalan.scoring import filters
from catalan.scoring.scorer import ARM_HEADLINE, ARM_PDF, arm_hash

logger = logging.getLogger(__name__)

RESULTS_DIR = ARCHIVE_ROOT / "results"
C0_SESSIONS = 40
C0_THRESHOLD = 0.70
C12_SESSIONS = 120
BUCKETS = 5
DSR_TRIALS = 5
EULER_GAMMA = 0.5772156649015329


# ── Costs (Amendment 1, "Cost c_t") ───────────────────────────────────────────

def explicit_cost_bps(notional: float = POSITION_NOTIONAL_INR) -> float:
    """Statutory and brokerage cost of one intraday round trip, in bps of notional."""
    brokerage = 2 * min(BROKERAGE_PER_ORDER_CAP, BROKERAGE_RATE * notional)
    stt = STT_SELL_RATE * notional
    exchange = 2 * EXCHANGE_TXN_RATE * notional
    stamp = STAMP_DUTY_BUY_RATE * notional
    sebi = 2 * SEBI_TURNOVER_RATE * notional
    gst = GST_RATE * (brokerage + exchange)
    return (brokerage + stt + exchange + stamp + sebi + gst) / notional * 1e4


def abdi_ranaldo_bps(window: Sequence[Optional[tuple]]) -> Optional[float]:
    """Abdi-Ranaldo (2017) window estimator, in bps, or None if under SPREAD_MIN_PAIRS.

    ``window`` is the SPREAD_WINDOW_SESSIONS consecutive grid sessions before t,
    oldest first; each element is (high, low, close) or None (no bar). A pair
    (τ, τ+1) counts only when both sessions carry a bar. On logs:
    s = sqrt(max(0, 4 · mean[(c_τ − η_τ)(c_τ − η_τ+1)])), η = (log h + log l) / 2.
    """
    terms = []
    for a, b in zip(window[:-1], window[1:]):
        if not a or not b:
            continue
        (ha, la, ca), (hb, lb, _) = a, b
        if min(ha, la, ca, hb, lb) <= 0:
            continue
        c = math.log(ca)
        eta_a = (math.log(ha) + math.log(la)) / 2
        eta_b = (math.log(hb) + math.log(lb)) / 2
        terms.append((c - eta_a) * (c - eta_b))
    if len(terms) < SPREAD_MIN_PAIRS:
        return None
    return math.sqrt(max(0.0, 4 * sum(terms) / len(terms))) * 1e4


# ── Eligibility and verdicts ──────────────────────────────────────────────────

def eligible_announcements(anns, sessions: List[date], universe: frozenset,
                           since: str, until: Optional[str] = None):
    """Eligible announcements with their attributed session t (Definitions)."""
    import pandas as pd

    day = anns["announced_at"].str[:10]
    rows = anns[(day >= since) & ((day <= until) if until else True) & anns["symbol"].isin(universe)]
    kept, _ = filters.apply_all(rows.to_dict("records"))
    # Filings that attribute past the last session with prices (after-close on
    # the last day, or later) have no session yet: left out, not crashed on.
    last = sessions[-1]
    kept = [r for r in kept
            if (ts := datetime.fromisoformat(r["announced_at"])).date() < last
            or (ts.date() == last and ts.time() < NEWS_CUTOFF)]
    out = []
    stamps = [datetime.fromisoformat(r["announced_at"]) for r in kept]
    attributed = attribution_dates(stamps, sessions) if stamps else []
    for r, ts, t in zip(kept, stamps, attributed):
        if classify_session(ts, sessions) != OVERNIGHT:
            continue
        out.append({**r, "session": t})
    return pd.DataFrame(out, columns=list(rows.columns) + ["session"])


def pick_verdicts(eligible, scores, model_id: str = PRIMARY_MODEL):
    """PDF arm's parsed verdict, else the headline arm's (fallback); else dropped."""
    s = scores[(scores["model_id"] == model_id) & (scores["temperature"] == TEMPERATURE)
               & scores["score"].notna()]
    pdf = s[s["prompt_hash"] == arm_hash(ARM_PDF)].set_index("announcement_id")["score"]
    head = s[s["prompt_hash"] == arm_hash(ARM_HEADLINE)].set_index("announcement_id")["score"]
    out = eligible.copy()
    out["verdict"] = out["id"].map(pdf)
    out["fallback"] = out["verdict"].isna() & out["id"].isin(head.index)
    out.loc[out["fallback"], "verdict"] = out.loc[out["fallback"], "id"].map(head)
    dropped = int(out["verdict"].isna().sum())
    return out[out["verdict"].notna()].copy(), dropped


def stock_session_scores(verdicts):
    """Mean verdict per (symbol, session)."""
    return verdicts.groupby(["symbol", "session"])["verdict"].mean()


# ── Validity, spreads, buckets ────────────────────────────────────────────────

def stock_sessions(panel, sessions: List[date], universe: frozenset, targets: List[date]):
    """One row per (study-universe symbol, target session) with validity and spread.

    Valid = a panel row with drift_ret and adv_20d, and a spread defined from at
    least SPREAD_MIN_PAIRS pairs in the SPREAD_WINDOW_SESSIONS sessions before t.
    """
    import pandas as pd

    p = panel[panel["symbol"].isin(universe)]
    bars = {(r.symbol, r.date): (r.high, r.low, r.close)
            for r in p.itertuples() if pd.notna(r.high) and pd.notna(r.low) and pd.notna(r.close)}
    rows = {(r.symbol, r.date): r for r in p.itertuples()}
    index = {d: i for i, d in enumerate(sessions)}
    out = []
    for t in targets:
        i = index[t]
        window = sessions[max(0, i - SPREAD_WINDOW_SESSIONS):i]
        prev = sessions[i - 1] if i > 0 else None
        for sym in sorted(universe):                    # deterministic tie order
            r = rows.get((sym, t))
            if r is None:
                continue
            spread = (abdi_ranaldo_bps([bars.get((sym, d)) for d in window])
                      if len(window) == SPREAD_WINDOW_SESSIONS else None)
            # ADV over the 20 sessions BEFORE t (verifier S1): the panel's
            # adv_20d on session t-1, so day t's own volume is never used.
            pr = rows.get((sym, prev)) if prev else None
            adv = pr.adv_20d if pr is not None else None
            valid = (pd.notna(r.drift_ret) and adv is not None and pd.notna(adv)
                     and spread is not None)
            out.append({"symbol": sym, "session": t, "drift_ret": r.drift_ret,
                        "init_ret": r.init_ret, "adv_20d": adv,
                        "spread_bps": spread, "valid": bool(valid)})
    frame = pd.DataFrame(out, columns=["symbol", "session", "drift_ret", "init_ret",
                                       "adv_20d", "spread_bps", "valid"])
    frame["bucket"] = None
    v = frame["valid"]
    if v.any():
        ranks = frame[v].groupby("session")["adv_20d"].rank(method="first")
        sizes = frame[v].groupby("session")["adv_20d"].transform("size")
        frame.loc[v, "bucket"] = ((ranks * BUCKETS - 1) // sizes + 1).astype(int)
    return frame


# ── The daily series ──────────────────────────────────────────────────────────

def _leg(basket, universe_rows):
    """(e, c) for a basket against a universe. Empty basket → (0, 0)."""
    if basket.empty:
        return 0.0, 0.0
    e = basket["drift_ret"].mean() - universe_rows["drift_ret"].mean()
    c = (explicit_cost_bps() + basket["spread_bps"].mean() + HEDGE_COST_BPS) / 1e4
    return float(e), float(c)


def daily_series(ss, scores_by_stock, universe_size: int, first_session: date):
    """One row per usable session ≥ first_session (Amendment 1, Definitions)."""
    import pandas as pd

    out = []
    for t, day in ss.groupby("session"):
        if t < first_session:
            continue
        # Usable: >= 80% of the universe has a drift_ret (Amendment 1). The
        # stricter "valid" test decides basket and universe membership only.
        if day["drift_ret"].notna().sum() < USABLE_SESSION_MIN_COVERAGE * universe_size:
            continue
        valid = day[day["valid"]]
        score = valid["symbol"].map(lambda s: scores_by_stock.get((s, t), float("nan"))).astype(float)
        yes, no = valid[score > 0], valid[score < 0]
        e, c = _leg(yes, valid)
        row = {"session": t, "e": e, "c": c, "n": e - c, "yes": len(yes), "no": len(no)}
        yi, ni = yes["init_ret"].dropna(), no["init_ret"].dropna()
        row["c0_obs"] = bool(len(yi) and len(ni))
        row["c0_hit"] = bool(row["c0_obs"] and (yi.mean() - ni.mean()) > 0)
        for k in range(1, BUCKETS + 1):
            ek, ck = _leg(yes[yes["bucket"] == k], valid[valid["bucket"] == k])
            row[f"n_b{k}"] = ek - ck
        e2, c2 = _leg(yes[yes["bucket"] >= 2], valid[valid["bucket"] >= 2])
        row["n_ex_b1"] = e2 - c2
        out.append(row)
    return pd.DataFrame(out).sort_values("session").reset_index(drop=True) if out else pd.DataFrame()


# ── Statistics ────────────────────────────────────────────────────────────────

def t_stat(x) -> float:
    n = len(x)
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / math.sqrt(n))) if n > 1 and sd > 0 else float("nan")


def deflated_sharpe(n, bucket_sharpes: Sequence[float], trials: int = DSR_TRIALS) -> float:
    """Bailey & López de Prado (2014), daily Sharpes, non-excess kurtosis."""
    from scipy.stats import kurtosis, norm, skew

    t = len(n)
    sr = n.mean() / n.std(ddof=1)
    var_sr = float(sum((s - sum(bucket_sharpes) / len(bucket_sharpes)) ** 2
                       for s in bucket_sharpes) / (len(bucket_sharpes) - 1))
    sr0 = math.sqrt(var_sr) * ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / trials)
                               + EULER_GAMMA * norm.ppf(1 - 1 / (trials * math.e)))
    g3 = float(skew(n))                                 # plain sample moments
    g4 = float(kurtosis(n, fisher=False))               # non-excess
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    return float(norm.cdf((sr - sr0) * math.sqrt(t - 1) / denom))


def evaluate_c0(series) -> Dict:
    obs = series[series["c0_obs"]].head(C0_SESSIONS)
    rate = float(obs["c0_hit"].mean()) if len(obs) else float("nan")
    return {"gate": "C0", "observations": int(len(obs)), "hit_rate": rate,
            "threshold": C0_THRESHOLD, "pass": bool(len(obs) == C0_SESSIONS and rate >= C0_THRESHOLD)}


def evaluate_c1c2(series) -> Dict:
    s = series.head(C12_SESSIONS)
    e, n = s["e"], s["n"]
    bucket_sr = [float(s[f"n_b{k}"].mean() / s[f"n_b{k}"].std(ddof=1))
                 if s[f"n_b{k}"].std(ddof=1) > 0 else 0.0 for k in range(1, BUCKETS + 1)]
    out = {
        "gate": "C1C2", "sessions": int(len(s)),
        "c1": {"mean_bps": float(e.mean() * 1e4), "t": t_stat(e)},
        "c2": {"mean_bps": float(n.mean() * 1e4), "t": t_stat(n),
               "sharpe_annual": float(n.mean() / n.std(ddof=1) * math.sqrt(252)) if n.std(ddof=1) > 0 else float("nan"),
               "dsr": deflated_sharpe(n, bucket_sr), "bucket_sharpes_daily": bucket_sr},
        "ex_bucket1_t": t_stat(s["n_ex_b1"]),
        "yes_basket_mean_size": float(s["yes"].mean()),
    }
    c1 = out["c1"]["mean_bps"] > 0 and out["c1"]["t"] >= 2.0
    c2 = out["c2"]
    c2_pass = (c2["t"] >= 2.0 and c2["mean_bps"] >= 5 and c2["sharpe_annual"] >= 0.5 and c2["dsr"] > 0.95)
    out["c1"]["pass"] = bool(c1)
    out["c2"]["pass"] = bool(c2_pass and len(s) == C12_SESSIONS)
    out["bucket1_only_negative"] = bool(c2_pass and out["ex_bucket1_t"] < 2.0)
    out["ship"] = bool(out["c2"]["pass"] and not out["bucket1_only_negative"])
    return out


# ── CLI: loaders and the enforced stopping rule ───────────────────────────────

def _load(since: str, until: str, conn):
    import pandas as pd

    from catalan.data.panel import load_panel, trading_sessions
    from catalan.data.universe import study_universe

    anns = pd.read_sql_query(
        "SELECT id, symbol, announced_at, headline, category, attachment_size FROM announcements "
        "WHERE substr(announced_at, 1, 10) >= ? AND substr(announced_at, 1, 10) <= ?",
        conn, params=(since, until))
    scores = pd.read_sql_query(
        "SELECT announcement_id, model_id, prompt_hash, temperature, score FROM scores", conn)
    from datetime import timedelta
    lo = (date.fromisoformat(since) - timedelta(days=60)).isoformat()
    sessions = trading_sessions(lo, until)
    # "The panel is rebuilt from price_history at evaluation" (Amendment 1).
    from catalan.data.panel import build_panel
    # Clear the window first: the panel upsert never deletes, so a row an
    # earlier build kept but this one drops would otherwise leak into the
    # evaluation (verifier review 8). Local cache only; `shortable` is unused in v2.
    with conn:
        conn.execute("DELETE FROM panel WHERE date >= ? AND date <= ?", (lo, until))
    build_panel(lo, until, conn=conn)
    panel = load_panel(lo, until, in_universe_only=False, conn=conn)
    window = panel[panel["date"] >= date.fromisoformat(since)]
    if window.empty or window["high"].isna().all():
        raise SystemExit(f"panel has no rows with high/low for {since}..{until}: the price "
                         "backfill does not cover the window. Nothing computed.")
    return anns, scores, sessions, panel, study_universe()


def run(since: str, until: str, first_session: date, conn) -> Dict:
    anns, scores, sessions, panel, uni = _load(since, until, conn)
    eligible = eligible_announcements(anns, sessions, uni, since, until)
    verdicts, dropped = pick_verdicts(eligible, scores)
    targets = [d for d in sessions if d >= first_session and d.isoformat() <= until]
    ss = stock_sessions(panel, sessions, uni, targets)
    series = daily_series(ss, stock_session_scores(verdicts).to_dict(), len(uni), first_session)
    counted = set(series["session"]) if len(series) else set()
    in_counted = eligible[eligible["session"].isin(counted)]
    v_counted = verdicts[verdicts["session"].isin(counted)]
    return {"eligible": int(len(in_counted)),
            "dropped_no_verdict": int(len(in_counted) - len(v_counted)),
            "fallback_share": float(v_counted["fallback"].mean()) if len(v_counted) else float("nan"),
            "series": series}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="catalan-v2 analysis (Amendment 1)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true", help="pilot window only (before STUDY_CLOCK_START)")
    mode.add_argument("--gate", choices=["C0", "C1C2"])
    ap.add_argument("--since")
    ap.add_argument("--until")
    a = ap.parse_args()

    from catalan.clock import today_ist
    from catalan.data.panel import trading_sessions
    from catalan.data.store import store

    conn = store()
    try:
        if a.pilot:
            since = a.since or "2026-08-07"
            until = min(a.until or today_ist(), "2026-10-25")
            sessions = trading_sessions(since, until)
            first = next(d for d in sessions if d.isoformat() > since)
            res = run(since, until, first, conn)
            s = res.pop("series")
            print(json.dumps({**res, "pilot_sessions": int(len(s)),
                              "c0_preview": evaluate_c0(s) if len(s) else None}, indent=2, default=str))
            return

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / f"{a.gate}.json"
        if path.exists():
            print(f"{a.gate} was already evaluated (stopping rule): {path}")
            print(path.read_text())
            return
        if a.gate == "C1C2":
            c0 = RESULTS_DIR / "C0.json"
            if not c0.exists() or not json.loads(c0.read_text()).get("pass"):
                raise SystemExit("C1/C2 are not evaluated: C0 has not passed "
                                 "(Amendment 1: a C0 fail stops the study).")
        sessions = trading_sessions(STUDY_CLOCK_START, today_ist())
        until = sessions[-1].isoformat()                # last session with prices
        first = next(d for d in sessions if d.isoformat() > STUDY_CLOCK_START)
        res = run(STUDY_CLOCK_START, until, first, conn)
        s = res.pop("series")
        if a.gate == "C0":
            n_obs = int(s["c0_obs"].sum()) if len(s) else 0
            if n_obs < C0_SESSIONS:
                raise SystemExit(f"NO-PEEK: C0 needs {C0_SESSIONS} usable sessions with both baskets; "
                                 f"{n_obs} exist. Nothing shown or written.")
            result = evaluate_c0(s)
        else:
            if len(s) < C12_SESSIONS:
                raise SystemExit(f"NO-PEEK: C1/C2 need {C12_SESSIONS} usable counted sessions; "
                                 f"{len(s)} exist. Nothing shown or written.")
            result = evaluate_c1c2(s)
        result.update({k: v for k, v in res.items()}, evaluated_at=today_ist())
        path.write_text(json.dumps(result, indent=2, default=str))
        print(json.dumps(result, indent=2, default=str))
        print(f"\nCOMMIT {path} NOW: write-once only holds once it is in git.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
