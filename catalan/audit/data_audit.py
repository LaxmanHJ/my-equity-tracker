"""
CATALAN data audit — everything collected since 2026-08-07, read-only.

Answers, from the data rather than from category names: what was collected, is
it clean, how much is duplicate or templated, which categories carry
company-specific information, and what the scoring spend actually bought.

**Read-only by construction.**
  - Reads the committed NDJSON archive (the system of record), never catalan.db.
  - Reads the NIFTY500 roster and the SET of symbols with price data from Turso,
    read-only. It never reads a price, a return, or anything derived from one:
    the NO-PEEK rule is in force until the amended pre-registration is committed,
    and nothing here joins a verdict to a market outcome.
  - Reads a fixed 145-PDF random sample from a local cache. The manifest
    (seq_id, URL, sha256) is written with the output, so the sample is
    reproducible; ``--fetch-missing`` re-downloads it by URL.
  - Writes only under catalan/audit/output/.

Run:
    python3 -m catalan.audit.data_audit
    python3 -m catalan.audit.data_audit --fetch-missing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from rapidfuzz.distance import DamerauLevenshtein

from catalan.config import CATEGORY_EXCLUSIONS, PROJECT_ROOT
from catalan.data.archive import ANNOUNCEMENTS_DIR, SCORES_DIR, read_ndjson
from catalan.scoring import filters
from catalan.scoring.prompt import PROMPT_TEMPLATE

OUT = Path(__file__).parent / "output"
PDF_CACHE = Path.home() / ".cache" / "catalan_audit_pdfs"
MANIFEST = OUT / "pdf_sample_manifest.csv"

# Cost basis. List prices for claude-haiku-4-5, the only model that scored this
# corpus. Token counts are ESTIMATED from text length (no usage was recorded per
# call), so every dollar figure here is an estimate on top of an actual call count.
PRICE_IN, PRICE_OUT = 1.0, 5.0        # $ per million tokens
CHARS_PER_TOKEN = 4.0
PROMPT_FIXED_CHARS = len(PROMPT_TEMPLATE.replace("_company_name_", "").replace("_headline_", ""))

TEMPLATE_MIN_SYMBOLS = 5              # residual text shared by >= this many companies = template
CROSS_DAY_SIMILARITY = 0.9            # re-filing detector, same symbol, within 3 days
CROSS_DAY_WINDOW = 3

IST_OFFSET = timedelta(hours=5, minutes=30)

# Step 12 scorecard bands: (High if >= first, Low if < second, else Med).
# Fixed before looking at the scorecard itself; each metric is defined in
# data_value_analysis.md. Categories below SCORECARD_MIN_RECORDS are not banded.
SCORECARD_MIN_RECORDS = 150
SCORECARD_BANDS = {
    "completeness_%": (98, 90),        # attachment present, or absent by design (exchange queries)
    "validity_%": (99, 95),            # non-empty headline and a parseable timestamp
    "uniqueness_%": (97, 90),          # 100 - strict duplicate %
    "specific_headline_%": (70, 30),   # headline not shared (masked) by >= 5 companies
    "informative_signal_%": (60, 30),  # directional verdict whose rationale does not say "no information"
    "stability_%": (75, 50),           # re-scored announcements whose verdict never changed (n >= 10)
}

# Evidence tiers for Steps 9-10. Chosen from the measured table, not from the
# names: these types combine a high label-only share with a high share of
# signals whose own rationale says "routine / no information", and the PDF
# sample shows short procedural documents. Candidates, not exclusions.
LOW_VALUE_TYPES = {"analyst_meet_notice", "analyst_meet_record", "shareholder_meeting", "routine_compliance"}
# Specific headlines, low no-information share, or (results/ratings) substance
# that sits in the PDF rather than the headline.
PRIORITY_TYPES = {"financial_results", "press_release", "business_operations", "m_and_a", "fund_raising",
                  "regulatory_legal", "corporate_action", "credit_rating"}

# ── Analytical information types ──────────────────────────────────────────────
# Analysis only: nothing here changes the data model or any filter. Categories
# with a specific meaning map directly; the generic ones ("General Updates",
# "Updates", "Shareholders meeting", ...) are classified from headline keywords.
TYPE_BY_CATEGORY = {
    "Integrated Filing- Financial": "financial_results",
    "Clarification - Financial Results": "financial_results",
    "Reply to Clarification- Financial results": "financial_results",
    "Reasons for Delayed/Non-submission of Financial Results": "financial_results",
    "Record Date": "corporate_action", "Revised Record date": "corporate_action",
    "Dividend": "corporate_action", "Bonus": "corporate_action", "Stock split": "corporate_action",
    "Buyback": "corporate_action", "Public Announcement - Buyback of Shares": "corporate_action",
    "Closure of Buy Back": "corporate_action", "Redemption": "corporate_action",
    "Forfeiture": "corporate_action", "Name Change": "corporate_action",
    "Name and Symbol Change": "corporate_action", "Increase in Authorised Capital": "corporate_action",
    "Conversion": "corporate_action",
    "Allotment of Securities": "fund_raising", "Issue of Securities": "fund_raising",
    "Qualified Institutional Placement": "fund_raising", "Preferential issue": "fund_raising",
    "Rights Issue": "fund_raising", "Offer for sale": "fund_raising",
    "Acquisition": "m_and_a", "Amalgamation/Merger": "m_and_a", "Scheme of Arrangement": "m_and_a",
    "Demerger": "m_and_a", "Sale or disposal": "m_and_a", "Other Restructuring": "m_and_a",
    "Diversification/Disinvestment": "m_and_a", "Corporate Debt Restructuring": "m_and_a",
    "One time settlement": "m_and_a", "Delisting": "m_and_a", "Voluntary Delisting": "m_and_a",
    "Public Announcement-Open Offer": "m_and_a",
    "Bagging/Receiving of orders/contracts": "business_operations",
    "Awarding of order(s)/contract(s)": "business_operations", "Agreements": "business_operations",
    "Memorandum of Understanding/Agreements": "business_operations",
    "Arrangements for strategic, technical, manufacturing, or marketing tie up": "business_operations",
    "Commencement of commercial production/operations": "business_operations",
    "Capacity addition": "business_operations", "Product launch": "business_operations",
    "Monthly Business Updates": "business_operations",
    "Postponement of commercial production/operations": "business_operations",
    "Adoption of new line(s) of business": "business_operations",
    "Closure of operations": "business_operations", "Disruption of Operations": "business_operations",
    "Strikes/Lockouts/Disturbances": "business_operations", "Rescission/termination(s)": "business_operations",
    "Granting/withdrawal/surrender/cancellation/suspension of key licenses/ regulatory approvals": "business_operations",
    "Giving guarantees/indemnity/ becoming a surety for third party": "business_operations",
    "Appointment": "management_change", "Change in Management": "management_change",
    "Resignation of Director/KMP/SMP": "management_change", "Resignation": "management_change",
    "Change in Director(s)": "management_change", "Cessation": "management_change",
    "Retirement": "management_change", "Change in Company Secretary/Compliance Officer": "management_change",
    "Demise": "management_change", "Change in Auditors": "management_change",
    "Resignation of Statutory Auditor": "management_change",
    "Action(s) taken or orders passed": "regulatory_legal",
    "Action(s) initiated or orders passed": "regulatory_legal",
    "Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company": "regulatory_legal",
    "Corporate Insolvency Resolution Process": "regulatory_legal", "Fraud/Default/Arrest": "regulatory_legal",
    "Delay/default in the payment of fines/penalties/dues etc. to authority": "regulatory_legal",
    "Effect(s) on listed entity due to changed regulatory  framework applicable": "regulatory_legal",
    "Suspension of Trading": "regulatory_legal",
    "Credit Rating": "credit_rating", "Credit Rating- Revision": "credit_rating",
    "Credit Rating- New": "credit_rating", "Credit Rating- Others": "credit_rating",
    "Disclosure under SEBI Takeover Regulations": "shareholding_insider",
    "Trading Plan under PIT": "shareholding_insider",
    "ESOP/ESOS/ESPS": "esop", "Options to purchase securities": "esop",
    "Investor Presentation": "investor_presentation",
    "Press Release": "press_release", "Press Release (Revised)": "press_release",
    "Extension of Annual General Meeting": "shareholder_meeting",
    "Spurt in Volume": "exchange_query", "Price movement": "exchange_query",
    "News Verification": "exchange_query", "Rumour Verification - Regulation 30(11)": "exchange_query",
    "Trading Window": "routine_compliance", "Copy of Newspaper Publication": "routine_compliance",
    "Certificate under SEBI (Depositories and Participants) Regulations, 2018": "routine_compliance",
    "Structural Digital Database": "routine_compliance", "Loss of Share Certificates": "routine_compliance",
    "Issue of Duplicate Share Certificate": "routine_compliance",
    "Statement of deviation(s) or variation(s) under Reg. 32": "routine_compliance",
    "Monitoring Agency Report": "routine_compliance", "Utilisation of Funds": "routine_compliance",
    "Registrar & Share Transfer Agent Update": "routine_compliance", "Address Change": "routine_compliance",
    "Integrated Filing- Governance": "routine_compliance", "Amendment to AOA/MOA": "routine_compliance",
    "Corrigendum": "correction", "Addendum": "correction", "Amendment(s)": "correction",
}

# Ordered: first match wins. Applied to generic categories only.
KEYWORD_RULES = [
    # Added after reading the 150-record validation sample (it caught these as
    # misses or mistypes); reported as such in the audit.
    ("routine_compliance", r"\bbrsr\b|business responsibility|\brta\b|registrar|re-?lodg|dispatch of|"
                           r"payment of interest|interest payment|code of conduct|\besg\b"),
    ("financial_results", r"financial result|quarterly result|results? for the (quarter|period)|unaudited|audited financial|q1 ?fy|earnings"),
    ("analyst_meet_notice", r"schedule of (the )?(analyst|investor|meet)|intimation of (the )?(analyst|investor|schedule)|investor meet|analyst meet|conference call|con\.? call|earnings call|investor conference|plant visit|nda meeting|group meet"),
    ("analyst_meet_record", r"transcript|recording|audio"),
    ("shareholder_meeting", r"annual general meeting|\bagm\b|\begm\b|extra.?ordinary general|postal ballot|e-?voting|scrutini[sz]er|voting result|book closure|annual report|proceedings of|shareholders meeting|notice of"),
    ("corporate_action", r"record date|dividend|bonus|split|sub-?division|buy.?back"),
    ("credit_rating", r"rating"),
    ("fund_raising", r"allotment|qualified institutional|\bqip\b|preferential|debenture|\bncd|warrant|rights issue|fund rais|commercial paper"),
    ("m_and_a", r"acqui|merger|amalgamation|demerger|scheme of arrangement|stake|joint venture|subsidiar"),
    ("business_operations", r"order|contract|letter of award|\bloa\b|agreement|\bmou\b|capacity|commission|launch|plant|project|approval|licen[cs]e"),
    ("management_change", r"appoint|resign|cessation|director|\bkmp\b|\bceo\b|\bcfo\b|auditor|company secretary|re-?designat"),
    ("regulatory_legal", r"penalt|order passed|\bgst\b|tax demand|income.?tax|litigation|show cause|\bnclt\b|court|arbitration|search|summons|notice from|demand"),
    ("shareholding_insider", r"insider trading|regulation 7\(2\)|\bpit\b|\bsast\b|pledge|encumbrance|takeover|shareholding"),
    ("esop", r"\besop|\besos|\besps|stock option"),
    ("press_release", r"press release|media release"),
    ("routine_compliance", r"trading window|newspaper|certificate|regulation 74|reg\.? ?74|loss of share|duplicate|regulation 36|compliance|esg"),
]
KEYWORD_RULES = [(t, re.compile(p, re.I)) for t, p in KEYWORD_RULES]
RESULTS_RE = dict(KEYWORD_RULES)["financial_results"]

# A directional verdict whose OWN rationale says there is nothing to go on.
# A proxy for "the output carries no information", validated by reading a
# sample (see classification_validation.md); it is not ground truth.
NO_INFO_RATIONALE = re.compile(
    r"\b(neutral|routine|procedural(ly)?|administrative(ly)?|formality|merely|"
    r"no (new |material |substantive |specific |concrete |actual )?(business|financial|operational)? ?"
    r"(information|details|developments|news|impact|substance)|no material|"
    r"without (knowing|any (specific|details|information|indication)|specific|details|disclos|providing|further)|"
    r"lack(s|ing)? (of )?(specific|detail|information|substantive)|"
    r"compliance (filing|requirement|disclosure)|standard (regulatory|compliance|disclosure|procedure))\b",
    re.I,
)

_RESIDUAL_RE = re.compile(r"^(.{0,160}?)\s+(has\s+(?:informed|submitted).*)$", re.I | re.S)
_MASK_RE = re.compile(
    r"\b\d{1,2}(st|nd|rd|th)?[-/ .]?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-/ ,.]*\d{2,4}\b|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4}\b|\d+", re.I)
_AMOUNT_RE = re.compile(r"(₹|\brs\.?\s?\d|\binr\b|crore|\bcr\b|lakh|million|billion|\d\s?%)", re.I)
_URL_TS_RE = re.compile(r"_(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})_")
_EMPTY_RESIDUAL_RE = re.compile(r"regarding\s*(''|\"\")\s*\.?$", re.I)


def pct(n, d) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


# ── Load ──────────────────────────────────────────────────────────────────────

def load():
    rows = []
    for path in sorted(ANNOUNCEMENTS_DIR.glob("*.ndjson.gz")):
        for r in read_ndjson(path):
            r["file_day"] = path.name[:10]
            rows.append(r)
    A = pd.DataFrame(rows)
    S = pd.DataFrame([r for p in sorted(SCORES_DIR.glob("*.ndjson.gz")) for r in read_ndjson(p)])
    return A, S


def ingest_clock_by_file() -> Dict[str, str]:
    """'UTC' if the day file was first added by the CI bot (GitHub runner clock),
    'local' otherwise. ingested_at is stored without a zone, so this is the only
    way to put it on the same clock as announced_at (IST)."""
    out = {}
    for path in sorted(ANNOUNCEMENTS_DIR.glob("*.ndjson.gz")):
        author = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%an", "--", str(path)],
            capture_output=True, text=True, cwd=PROJECT_ROOT).stdout.strip().splitlines()
        out[path.name[:10]] = "UTC" if author and author[-1] == "catalan-bot" else "local"
    return out


# ── Derivations ───────────────────────────────────────────────────────────────

def residual(headline: str) -> str:
    """The headline minus the filer's name, whatever form the name takes."""
    h = re.sub(r"\s+", " ", headline or "").strip()
    m = _RESIDUAL_RE.match(h)
    return m.group(2) if m else h


def norm_residual(text: str) -> str:
    return re.sub(r"[\s.]+$", "", re.sub(r"\s+", " ", text.lower())).strip()


def classify(category: str, headline: str) -> str:
    if category in TYPE_BY_CATEGORY:
        return TYPE_BY_CATEGORY[category]
    if category == "Analysts/Institutional Investor Meet/Con. Call Updates":
        return ("analyst_meet_record" if re.search(r"transcript|recording|audio|video", headline, re.I)
                else "analyst_meet_notice")
    if category == "Outcome of Board Meeting":
        return ("financial_results" if RESULTS_RE.search(headline)
                else "board_meeting_outcome")
    if category == "Shareholders meeting":
        return "shareholder_meeting"
    res = residual(headline)
    for t, rx in KEYWORD_RULES:
        if rx.search(res):
            return t
    return "generic_unclassified"


def size_bytes(s) -> Optional[float]:
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"^\s*([\d.]+)\s*(KB|MB|GB|B)\s*$", s, re.I)
    if not m:
        return None
    mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}[m.group(2).upper()]
    return float(m.group(1)) * mult


def enrich(A: pd.DataFrame, clocks: Dict[str, str]) -> pd.DataFrame:
    A = A.copy()
    fmt = "%Y-%m-%dT%H:%M:%S"
    A["ann_ts"] = pd.to_datetime(A["announced_at"], format=fmt, errors="coerce")
    A["dis_ts"] = pd.to_datetime(A["disseminated_at"], format=fmt, errors="coerce")
    A["ing_raw"] = pd.to_datetime(A["ingested_at"], format=fmt, errors="coerce")
    A["ingest_clock"] = A["file_day"].map(clocks)
    A["ing_ist"] = A["ing_raw"] + A["ingest_clock"].eq("UTC") * IST_OFFSET
    A["ann_day"] = A["ann_ts"].dt.date.astype(str)
    A["residual"] = A["headline"].map(residual)
    A["res_norm"] = A["residual"].map(norm_residual)
    A["res_masked"] = A["res_norm"].map(lambda t: _MASK_RE.sub("#", t))
    A["info_type"] = [classify(c or "", h or "") for c, h in zip(A["category"], A["headline"])]
    A["excluded_by_filter"] = [filters.is_excluded({"category": c, "headline": h})
                               for c, h in zip(A["category"], A["headline"])]
    A["size_bytes"] = A["attachment_size"].map(size_bytes)
    A["url_ext"] = A["attachment_url"].fillna("").str.extract(r"\.([A-Za-z0-9]{2,5})$")[0].str.lower()
    A["has_amount"] = A["residual"].map(lambda t: bool(_AMOUNT_RE.search(t or "")))

    # Templating: residual text shared by many different companies says nothing
    # company-specific. Verbatim sharing = label-only; sharing after masking
    # numbers and dates = templated (a superset).
    for col, flag in (("res_norm", "label_only"), ("res_masked", "templated")):
        n_sym = A.groupby(col)["symbol"].nunique()
        A[flag] = A[col].map(n_sym).ge(TEMPLATE_MIN_SYMBOLS)
    A["empty_headline"] = A["residual"].map(
        lambda t: bool(_EMPTY_RESIDUAL_RE.search(t or "")) or len((t or "").strip()) < 5)
    return A


# ── Duplicates ────────────────────────────────────────────────────────────────

def duplicate_flags(A: pd.DataFrame) -> pd.DataFrame:
    """Duplicate rules, strongest first. Text identity alone is NOT treated as
    duplication when the text is a template: two "Schedule of meet" filings by
    one company on one day are usually two meetings. Where the headline cannot
    tell documents apart, NSE's own attachment byte size is used instead."""
    A = A.sort_values(["ann_ts", "seq_id"]).copy()
    A["hl_norm"] = A["headline"].map(filters.normalise_headline)
    url = A["attachment_url"].where(A["attachment_url"].fillna("").str.startswith("https://"))
    size = A["attachment_size"].where(~A["attachment_size"].isin(["0 Bytes"]))
    A["_url"], A["_size"] = url, size

    A["dup_key"] = A.duplicated(["symbol", "seq_id"], keep="first")
    A["dup_seq_other_symbol"] = A.duplicated(["seq_id"], keep="first") & ~A["dup_key"]
    A["dup_same_url"] = A["_url"].notna() & A.duplicated(["_url"], keep="first")
    A["dup_same_ts_headline"] = A.duplicated(["symbol", "announced_at", "hl_norm"], keep="first")
    A["dup_same_text_same_size"] = A["_size"].notna() & A.duplicated(["symbol", "hl_norm", "_size"], keep="first")
    A["dup_exact"] = A[["dup_key", "dup_seq_other_symbol", "dup_same_url", "dup_same_ts_headline",
                        "dup_same_text_same_size"]].any(axis=1)
    # Same text, same symbol-day, but a DIFFERENT document size: reported, not
    # counted as duplication — most are separate filings under a template headline.
    A["repeat_text_different_doc"] = A.duplicated(["symbol", "ann_day", "hl_norm"], keep="first") & ~A["dup_exact"]

    def near(groups, same_day: bool):
        hits = set()
        for _, g in groups:
            g = g[~g["dup_exact"] & ~g["label_only"]].sort_values("ann_ts")
            items = list(zip(g.index, g["ann_ts"], g["res_norm"], g["ann_day"]))
            for i, (idx, ts, rn, day) in enumerate(items):
                for jdx, ts2, rn2, day2 in items[:i]:
                    if not same_day and (day2 == day or (ts - ts2).days > CROSS_DAY_WINDOW):
                        continue
                    if DamerauLevenshtein.normalized_similarity(rn, rn2) >= CROSS_DAY_SIMILARITY:
                        hits.add(idx)
                        break
        return hits

    A["dup_near_same_day"] = A.index.isin(near(A.groupby(["symbol", "ann_day", "category"]), True))
    A["dup_cross_day"] = A.index.isin(near(A.groupby("symbol"), False)) & ~A["dup_near_same_day"]
    A["dup_any"] = A["dup_exact"] | A["dup_near_same_day"] | A["dup_cross_day"]

    # The project's declared dedup rule, measured rather than trusted: what it
    # drops before scoring, and how often the dropped row's category differs
    # from its keeper's (a direct sign it is not the same announcement).
    recs = [{"id": i, "symbol": s, "announced_at": t, "headline": h, "category": c, "attachment_size": z}
            for i, s, t, h, c, z in zip(A.index, A["symbol"], A["announced_at"], A["headline"],
                                        A["category"], A["attachment_size"])]
    kept, dropped = filters.drop_near_duplicates(recs)
    by_key = defaultdict(list)
    for r in kept:
        by_key[filters.dedup_key(r["symbol"], r["announced_at"])].append(r)
    keeper_cat = {}
    for d in dropped:
        nd = filters.normalise_headline(d["headline"])
        best = max(by_key[filters.dedup_key(d["symbol"], d["announced_at"])],
                   key=lambda k: DamerauLevenshtein.normalized_similarity(nd, filters.normalise_headline(k["headline"])))
        keeper_cat[d["id"]] = best["category"]
    A["project_filter_drop"] = A.index.isin(keeper_cat)
    A["project_filter_drop_other_category"] = [i in keeper_cat and keeper_cat[i] != c
                                               for i, c in zip(A.index, A["category"])]
    return A.drop(columns=["_url", "_size"]).sort_index()


# ── Scores ────────────────────────────────────────────────────────────────────

def score_view(S: pd.DataFrame) -> pd.DataFrame:
    """One row per announcement: its canonical verdict and its call history.

    Canonical = the first REAL verdict by scored_at, the same rule the fixed
    rebuild applies. Empty rows (NULL score and NULL rationale) are failed API
    calls from the 2026-08 outage: not observations, and not billed.
    """
    S = S.copy()
    S["rationale"] = S["rationale"].fillna("")
    S["real"] = S["score"].notna() | S["rationale"].ne("")
    S = S.sort_values("scored_at")
    real = S[S["real"]]
    first = real.groupby(["symbol", "seq_id"]).first()
    agg = real.groupby(["symbol", "seq_id"]).agg(
        n_real_calls=("score", "size"),
        n_distinct_verdicts=("score", lambda s: s.fillna(-9).nunique()))
    empties = S[~S["real"]].groupby(["symbol", "seq_id"]).size().rename("n_empty_rows")
    V = first[["score", "rationale", "scored_at"]].join(agg).join(empties, how="outer")
    V["n_real_calls"] = V["n_real_calls"].fillna(0).astype(int)
    V["n_empty_rows"] = V["n_empty_rows"].fillna(0).astype(int)
    V["processed"] = V["n_real_calls"] > 0
    V["out_chars"] = real.assign(c=real["rationale"].str.len() + 4).groupby(["symbol", "seq_id"])["c"].sum()
    return V.reset_index()


def est_cost(in_chars: float, out_chars: float, calls: int = 1) -> float:
    tin = calls * (PROMPT_FIXED_CHARS / CHARS_PER_TOKEN) + in_chars / CHARS_PER_TOKEN
    tout = out_chars / CHARS_PER_TOKEN + calls
    return tin * PRICE_IN / 1e6 + tout * PRICE_OUT / 1e6


# ── Reference data (read-only, no prices) ─────────────────────────────────────

def reference_sets(days: List[str]):
    from catalan.data import universe
    from catalan.data.store import turso_ro
    members = {d: universe.members_on(d) for d in days}
    conn = turso_ro()
    priced = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM price_history").fetchall()}
    return members, priced, str(universe.roster_last_effective_from())


# ── PDF sample ────────────────────────────────────────────────────────────────

BOILERPLATE_RE = re.compile(
    r"dear sir|madam|regulation 30|listing obligations|scrip code|bse limited|national stock exchange|"
    r"thanking you|yours faithfully|yours truly|company secretary|on record|phiroze|bandra.kurla|"
    r"dalal street|compliance officer|pursuant to", re.I)


def pdf_sample(A: pd.DataFrame, fetch_missing: bool) -> Optional[pd.DataFrame]:
    import fitz
    from catalan.data import pdf_text
    if not MANIFEST.exists():
        return None
    man = pd.read_csv(MANIFEST, dtype=str)
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    session = None
    rows = []
    for m in man.itertuples():
        path = PDF_CACHE / f"{m.seq_id}.pdf"
        if not path.exists() and fetch_missing and isinstance(m.attachment_url, str):
            import requests
            from catalan.data.collect_daily import HEADERS
            if session is None:
                session = requests.Session()
                session.headers.update(HEADERS)
            data = pdf_text.fetch(session, m.attachment_url)
            if data:
                path.write_bytes(data)
        if not path.exists():
            rows.append({"seq_id": m.seq_id, "stratum": m.stratum, "status": "missing_from_cache"})
            continue
        data = path.read_bytes()
        res = pdf_text.extract(data)
        row = {"seq_id": m.seq_id, "stratum": m.stratum, "status": res["status"],
               "sha256_match": (not isinstance(m.sha256, str)) or res["sha256"] == m.sha256,
               "total_pages": res["total_pages"],
               "window_kinds": json.dumps(res["page_kinds"]) if res["page_kinds"] else None}
        if res["status"] != "not_pdf":
            with fitz.open(stream=data, filetype="pdf") as doc:
                texts = [p.get_text() for p in doc]
            row["p1_cover_letter"] = len(BOILERPLATE_RE.findall(texts[0] if texts else "")) >= 5
            row["tok_first3"] = sum(len(t.strip()) for t in texts[:3]) / CHARS_PER_TOKEN
            row["tok_whole"] = sum(len(t.strip()) for t in texts) / CHARS_PER_TOKEN
        rows.append(row)
    P = pd.DataFrame(rows).merge(
        A[["seq_id", "symbol", "category", "info_type", "label_only"]], on="seq_id", how="left")
    return P


# ── Report builders ───────────────────────────────────────────────────────────

def md_table(df: pd.DataFrame, floatfmt: str = "{:,.1f}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        cells = []
        for v in row:
            if isinstance(v, float):
                cells.append("" if pd.isna(v) else floatfmt.format(v))
            elif isinstance(v, (int,)) and not isinstance(v, bool):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def field_completeness(A: pd.DataFrame) -> pd.DataFrame:
    checks = {
        "symbol": lambda s: s.str.fullmatch(r"[A-Z0-9&\-\._]+"),
        # INE equity, INF funds, IN9 partly-paid / DVR classes; IN0000000000 is a placeholder
        "isin": lambda s: s.str.fullmatch(r"IN[EF9][A-Z0-9]{9}"),
        "company": lambda s: s.str.len().gt(2),
        "announced_at": lambda s: pd.to_datetime(s, format="%Y-%m-%dT%H:%M:%S", errors="coerce").notna(),
        "disseminated_at": lambda s: pd.to_datetime(s, format="%Y-%m-%dT%H:%M:%S", errors="coerce").notna(),
        "ingested_at": lambda s: pd.to_datetime(s, format="%Y-%m-%dT%H:%M:%S", errors="coerce").notna(),
        "headline": lambda s: s.str.strip().str.len().gt(0),
        "category": lambda s: s.str.strip().str.len().gt(0),
        "industry": lambda s: s.str.strip().str.len().gt(0),
        "seq_id": lambda s: s.str.fullmatch(r"\d+"),
        "attachment_url": lambda s: s.str.startswith("https://nsearchives.nseindia.com/"),
        "attachment_size": lambda s: s.map(size_bytes).notna(),
        "has_xbrl": lambda s: s.isin([0, 1]),
        "source": lambda s: s.eq("nse_announcements"),
    }
    out = []
    n = len(A)
    for col, valid in checks.items():
        s = A[col]
        nonnull = s.notna()
        empty = s.astype(str).str.strip().eq("") & nonnull
        present = s[nonnull & ~empty]
        invalid = int((~valid(present.astype(str) if col not in ("has_xbrl",) else present)).sum())
        out.append({"field": col, "total": n, "non_null": int(nonnull.sum()),
                    "null": int((~nonnull).sum()), "null_%": pct((~nonnull).sum(), n),
                    "empty_string_%": pct(empty.sum(), n), "invalid": invalid,
                    "invalid_%": pct(invalid, n)})
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="CATALAN data audit (read-only)")
    ap.add_argument("--fetch-missing", action="store_true", help="re-download sample PDFs by URL")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    M: Dict = {}   # every headline number, for the report

    A, S = load()
    A = enrich(A, ingest_clock_by_file())
    A = duplicate_flags(A)
    V = score_view(S)
    A = A.merge(V, on=["symbol", "seq_id"], how="left")
    A["processed"] = A["processed"].fillna(False).astype(bool)
    A["n_real_calls"] = A["n_real_calls"].fillna(0).astype(int)
    A["n_empty_rows"] = A["n_empty_rows"].fillna(0).astype(int)
    A["verdict"] = A["score"].map({1: "YES", 0: "UNKNOWN", -1: "NO"}).where(A["processed"], None)
    A.loc[A["processed"] & A["score"].isna(), "verdict"] = "ABSTAIN"
    A["signal"] = A["verdict"].isin(["YES", "NO"])
    A["no_info_signal"] = A["signal"] & A["rationale"].fillna("").map(lambda t: bool(NO_INFO_RATIONALE.search(t)))
    A["flipped"] = A["n_distinct_verdicts"].fillna(1).gt(1)

    days = sorted(A["ann_day"].dropna().unique())
    members, priced, roster_asof = reference_sets(days)
    A["pit_nifty500"] = [s in members.get(d, ()) for s, d in zip(A["symbol"], A["ann_day"])]
    A["has_price_data"] = A["symbol"].isin(priced)

    n = len(A)
    M["records"] = n
    M["window"] = [days[0], days[-1]]
    M["symbols"] = int(A["symbol"].nunique())
    M["categories"] = int(A["category"].nunique())
    M["roster_last_effective_from"] = roster_asof

    # ── Step 2: completeness ──
    FC = field_completeness(A)
    FC.to_csv(OUT / "field_completeness.csv", index=False)
    sym_isin = A.dropna(subset=["isin"]).groupby("symbol")["isin"].nunique()
    isin_sym = A.dropna(subset=["isin"]).groupby("isin")["symbol"].nunique()
    sym_co = A.groupby("symbol")["company"].nunique()
    M["symbols_with_multiple_isins"] = sorted(sym_isin[sym_isin > 1].index.tolist())
    M["isins_with_multiple_symbols"] = sorted(isin_sym[isin_sym > 1].index.tolist())
    M["symbols_with_multiple_company_names"] = int((sym_co > 1).sum())
    M["empty_headlines"] = int(A["empty_headline"].sum())
    M["url_ext"] = A["url_ext"].fillna("(none)").value_counts().to_dict()
    M["has_xbrl_equals_has_attachment"] = bool((A["has_xbrl"].eq(1) == A["attachment_url"].notna()).all())
    sz = A["size_bytes"].dropna()
    M["attachment_size_bytes"] = {"median": float(sz.median()), "p90": float(sz.quantile(.9)),
                                  "p99": float(sz.quantile(.99)), "max": float(sz.max()),
                                  "under_20KB": int((sz < 20 * 1024).sum()),
                                  "over_20MB": int((sz > 20 * 1024 ** 2).sum())}

    # ── Step 3: timestamps ──
    lag_dis = (A["dis_ts"] - A["ann_ts"]).dt.total_seconds()
    lag_ing = (A["ing_ist"] - A["ann_ts"]).dt.total_seconds() / 3600
    url_ts = A["attachment_url"].fillna("").str.extract(_URL_TS_RE)
    url_dt = pd.to_datetime(url_ts[2] + "-" + url_ts[1] + "-" + url_ts[0] + "T" + url_ts[3] + ":" +
                            url_ts[4] + ":" + url_ts[5], format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    lag_url = (A["ann_ts"] - url_dt).dt.total_seconds()
    t = A["ann_ts"].dt.time
    import datetime as _dt
    session = pd.cut(A["ann_ts"].dt.hour * 60 + A["ann_ts"].dt.minute,
                     bins=[-1, 539, 554, 929, 1440],
                     labels=["00:00-09:00 (pre-open, tradable at open)", "09:00-09:15 (after cutoff)",
                             "09:15-15:30 (market hours)", "15:30-24:00 (post-close)"])
    seq_num = pd.to_numeric(A["seq_id"], errors="coerce")
    order = A.assign(seq=seq_num).dropna(subset=["seq", "ann_ts"]).sort_values("seq")
    inversions = int((order["ann_ts"].diff().dt.total_seconds() < -60).sum())
    ts_counts = A["announced_at"].value_counts()
    sym_ts = A.groupby(["symbol", "announced_at"]).size()
    M["timestamps"] = {
        "announced_at_unparseable": int(A["ann_ts"].isna().sum()),
        "disseminated_at_unparseable": int(A["dis_ts"].isna().sum()),
        "ingested_at_unparseable": int(A["ing_raw"].isna().sum()),
        "announced_day_ne_file_day": int((A["ann_day"] != A["file_day"]).sum()),
        "exact_midnight": int((t == _dt.time(0, 0, 0)).sum()),
        "dissemination_lag_s": {"negative": int((lag_dis < 0).sum()), "zero": int((lag_dis == 0).sum()),
                                "1s": int((lag_dis == 1).sum()), "2-60s": int(((lag_dis > 1) & (lag_dis <= 60)).sum()),
                                "1-60min": int(((lag_dis > 60) & (lag_dis <= 3600)).sum()),
                                "over_1h": int((lag_dis > 3600).sum()), "null": int(lag_dis.isna().sum()),
                                "max_h": round(float(lag_dis.max()) / 3600, 1)},
        "ingest_clock_files": dict(Counter(A.drop_duplicates("file_day")["ingest_clock"])),
        "ingested_before_announced_after_tz_fix": int((lag_ing < 0).sum()),
        "ingested_before_announced_naive": int(((A["ing_raw"] - A["ann_ts"]).dt.total_seconds() < 0).sum()),
        "ingest_lag_h": {"median": round(float(lag_ing.median()), 1), "p90": round(float(lag_ing.quantile(.9)), 1),
                         "max": round(float(lag_ing.max()), 1)},
        "url_timestamp_parsed": int(url_dt.notna().sum()),
        "announced_minus_url_upload_s": {"median": float(lag_url.median()), "p10": float(lag_url.quantile(.1)),
                                         "p90": float(lag_url.quantile(.9)), "negative": int((lag_url < 0).sum())},
        "session_bucket": {str(k): int(v) for k, v in session.value_counts().sort_index().items()},
        "weekend_records": int(A["ann_ts"].dt.dayofweek.ge(5).sum()),
        "seq_order_inversions_gt_60s": inversions,
        "timestamps_shared_by_5plus_records": int((ts_counts >= 5).sum()),
        "max_records_on_one_timestamp": int(ts_counts.max()),
        "symbol_timestamp_pairs_with_2plus": int((sym_ts >= 2).sum()),
    }
    daily = A.groupby("ann_day").agg(records=("seq_id", "size"), first=("ann_ts", "min"), last=("ann_ts", "max"),
                                     weekday=("ann_ts", lambda s: s.iloc[0].strftime("%a")),
                                     ingest_clock=("ingest_clock", "first")).reset_index()
    daily["first"] = daily["first"].dt.strftime("%H:%M")
    daily["last"] = daily["last"].dt.strftime("%H:%M")
    all_days = pd.date_range(days[0], days[-1], freq="D")
    M["missing_weekdays"] = [d.strftime("%Y-%m-%d") for d in all_days
                             if d.dayofweek < 5 and d.strftime("%Y-%m-%d") not in set(days)]
    M["missing_weekend_days"] = [d.strftime("%Y-%m-%d") for d in all_days
                                 if d.dayofweek >= 5 and d.strftime("%Y-%m-%d") not in set(days)]
    daily.to_csv(OUT / "daily_coverage.csv", index=False)

    # ── Step 4: duplicates ──
    dup_cols = ["dup_key", "dup_seq_other_symbol", "dup_same_url", "dup_same_ts_headline",
                "dup_same_text_same_size", "dup_exact", "dup_near_same_day", "dup_cross_day", "dup_any",
                "repeat_text_different_doc", "project_filter_drop", "project_filter_drop_other_category"]
    M["duplicates"] = {c: int(A[c].sum()) for c in dup_cols}
    M["duplicates"]["unique_records"] = int((~A["dup_any"]).sum())
    M["duplicates"]["dup_any_%"] = pct(A["dup_any"].sum(), n)
    M["templated_headlines"] = {
        "label_only": int(A["label_only"].sum()), "label_only_%": pct(A["label_only"].sum(), n),
        "templated": int(A["templated"].sum()), "templated_%": pct(A["templated"].sum(), n),
        "specific": int((~A["templated"]).sum()), "specific_%": pct((~A["templated"]).sum(), n),
        "with_amount_or_pct": int(A["has_amount"].sum())}
    examples = []
    for col in ["dup_same_url", "dup_same_ts_headline", "dup_same_text_same_size", "dup_near_same_day",
                "dup_cross_day", "repeat_text_different_doc", "project_filter_drop_other_category"]:
        for r in A[A[col]].sample(min(4, int(A[col].sum())), random_state=1).itertuples():
            examples.append({"rule": col, "symbol": r.symbol, "announced_at": r.announced_at,
                             "category": r.category, "headline": (r.headline or "")[:140]})
    pd.DataFrame(examples).to_csv(OUT / "duplicate_examples.csv", index=False)

    # ── Scores (AI output) ──
    real_calls = int(A["n_real_calls"].sum())
    M["ai"] = {
        "score_rows_in_archive": int(len(S)),
        "empty_failed_call_rows": int(A["n_empty_rows"].sum()),
        "real_calls": real_calls,
        "announcements_processed": int(A["processed"].sum()),
        "redundant_rescore_calls": int((A["n_real_calls"] - 1).clip(lower=0).sum()),
        "announcements_rescored": int(A["n_real_calls"].gt(1).sum()),
        "rescored_with_verdict_flip": int((A["n_real_calls"].gt(1) & A["flipped"]).sum()),
        "not_processed_not_excluded": int((~A["processed"] & ~A["excluded_by_filter"]).sum()),
        "processed_but_excluded_category": int((A["processed"] & A["excluded_by_filter"]).sum()),
        "verdicts": A.loc[A["processed"], "verdict"].value_counts().to_dict(),
        "no_info_signals": int(A["no_info_signal"].sum()),
        "no_info_signal_%_of_signals": pct(A["no_info_signal"].sum(), A["signal"].sum()),
        "signals_on_label_only_headlines": int((A["signal"] & A["label_only"]).sum()),
        "signals_on_label_only_headlines_%": pct((A["signal"] & A["label_only"]).sum(), A["signal"].sum()),
        "signal_%_label_only": pct((A["signal"] & A["label_only"]).sum(), (A["processed"] & A["label_only"]).sum()),
        "signal_%_specific": pct((A["signal"] & ~A["templated"]).sum(), (A["processed"] & ~A["templated"]).sum()),
        "no_%_label_only": pct((A["verdict"].eq("NO") & A["label_only"]).sum(), (A["processed"] & A["label_only"]).sum()),
        "no_%_specific": pct((A["verdict"].eq("NO") & ~A["templated"]).sum(), (A["processed"] & ~A["templated"]).sum()),
        "yes_%_label_only": pct((A["verdict"].eq("YES") & A["label_only"]).sum(), (A["processed"] & A["label_only"]).sum()),
        "yes_%_specific": pct((A["verdict"].eq("YES") & ~A["templated"]).sum(), (A["processed"] & ~A["templated"]).sum()),
    }
    # Stability: the same headline, prompt and model, scored on different days.
    Sr = S[S["score"].notna() | S["rationale"].fillna("").ne("")]
    sets = Sr.groupby(["symbol", "seq_id"])["score"].agg(
        lambda x: tuple(sorted(set(int(v) if pd.notna(v) else 9 for v in x))))
    calls = Sr.groupby(["symbol", "seq_id"]).size()
    rescored = sets[calls > 1]
    modal_share = Sr[Sr.set_index(["symbol", "seq_id"]).index.isin(rescored.index)].groupby(
        ["symbol", "seq_id"])["score"].agg(lambda x: x.value_counts(dropna=False).iloc[0] / len(x))
    M["ai"]["rescored_verdict_sets"] = {str(k): int(v) for k, v in Counter(rescored).most_common(8)}
    M["ai"]["rescored_yes_and_no_both_seen"] = int(sum(1 for t in rescored if 1 in t and -1 in t))
    M["ai"]["rescored_mean_modal_agreement_%"] = round(float(modal_share.mean()) * 100, 1)
    M["ai"]["rescored_calls_per_announcement_median"] = float(calls[calls > 1].median())

    in_chars = (A["company"].fillna("").str.len() + A["headline"].fillna("").str.len())
    A["est_cost_actual"] = [est_cost(ic * c, oc, c) if c else 0.0
                            for ic, oc, c in zip(in_chars, A["out_chars"].fillna(0), A["n_real_calls"])]
    one_out = A["out_chars"].fillna(0) / A["n_real_calls"].replace(0, 1)
    A["est_cost_once"] = [est_cost(ic, oc, 1) if p else 0.0
                          for ic, oc, p in zip(in_chars, one_out, A["processed"])]
    M["ai"]["est_cost_actual_usd"] = round(float(A["est_cost_actual"].sum()), 2)
    M["ai"]["est_cost_if_scored_once_usd"] = round(float(A["est_cost_once"].sum()), 2)
    M["ai"]["est_cost_per_call_usd"] = round(float(A["est_cost_actual"].sum()) / max(real_calls, 1), 6)

    # ── Universe / usability ──
    M["universe"] = {"pit_nifty500_records": int(A["pit_nifty500"].sum()),
                     "pit_nifty500_%": pct(A["pit_nifty500"].sum(), n),
                     "records_symbol_has_price_data": int(A["has_price_data"].sum()),
                     "records_symbol_has_price_data_%": pct(A["has_price_data"].sum(), n),
                     "processed_calls_on_non_nifty500": int(A.loc[~A["pit_nifty500"], "n_real_calls"].sum())}

    # ── Step 6 / 8: category table ──
    g = A.groupby("category")
    cat = pd.DataFrame({
        "count": g.size(),
        "info_type": g["info_type"].agg(lambda s: s.value_counts().index[0]),
        "excluded_now": g["excluded_by_filter"].mean() * 100,
        "dup_%": g["dup_any"].mean() * 100,
        "project_filter_drop_%": g["project_filter_drop"].mean() * 100,
        "no_attachment_%": g["attachment_url"].apply(lambda s: (s.isna() | s.eq("-")).mean() * 100),
        "label_only_%": g["label_only"].mean() * 100,
        "templated_%": g["templated"].mean() * 100,
        "pit_nifty500_%": g["pit_nifty500"].mean() * 100,
        "ai_processed": g["processed"].sum(),
        "ai_calls": g["n_real_calls"].sum(),
        "signal_%": g.apply(lambda d: pct(d["signal"].sum(), d["processed"].sum())),
        "yes_%": g.apply(lambda d: pct(d["verdict"].eq("YES").sum(), d["processed"].sum())),
        "no_%": g.apply(lambda d: pct(d["verdict"].eq("NO").sum(), d["processed"].sum())),
        "no_info_signal_%": g.apply(lambda d: pct(d["no_info_signal"].sum(), d["signal"].sum())),
        "signals_on_label_only_%": g.apply(lambda d: pct((d["signal"] & d["label_only"]).sum(), d["signal"].sum())),
        "rescored_n": g["n_real_calls"].apply(lambda s: int((s > 1).sum())),
        "flip_%_of_rescored": g.apply(lambda d: pct((d["flipped"] & d["n_real_calls"].gt(1)).sum(),
                                                     d["n_real_calls"].gt(1).sum())),
        "est_cost_usd": g["est_cost_actual"].sum(),
    }).sort_values("count", ascending=False)
    cat.insert(1, "%_total", cat["count"] / n * 100)
    cat = cat.reset_index()
    cat.to_csv(OUT / "data_category_analysis.csv", index=False)

    # ── Step 7 / 9 / 10: information-type table ──
    g = A.groupby("info_type")
    it = pd.DataFrame({
        "count": g.size(),
        "categories": g["category"].nunique(),
        "dup_%": g["dup_any"].mean() * 100,
        "label_only_%": g["label_only"].mean() * 100,
        "specific_headline_%": g["templated"].apply(lambda s: (~s).mean() * 100),
        "with_amount_%": g["has_amount"].mean() * 100,
        "pit_nifty500_%": g["pit_nifty500"].mean() * 100,
        "ai_processed": g["processed"].sum(),
        "ai_calls": g["n_real_calls"].sum(),
        "signal_%": g.apply(lambda d: pct(d["signal"].sum(), d["processed"].sum())),
        "no_info_signal_%": g.apply(lambda d: pct(d["no_info_signal"].sum(), d["signal"].sum())),
        "signals_on_label_only_%": g.apply(lambda d: pct((d["signal"] & d["label_only"]).sum(), d["signal"].sum())),
        "yes_%": g.apply(lambda d: pct(d["verdict"].eq("YES").sum(), d["processed"].sum())),
        "no_%": g.apply(lambda d: pct(d["verdict"].eq("NO").sum(), d["processed"].sum())),
        "flip_%_of_rescored": g.apply(lambda d: pct((d["flipped"] & d["n_real_calls"].gt(1)).sum(),
                                                     d["n_real_calls"].gt(1).sum())),
        "rescored_n": g["n_real_calls"].apply(lambda s: int((s > 1).sum())),
        "est_cost_usd": g["est_cost_actual"].sum(),
    }).sort_values("count", ascending=False)
    it.insert(1, "%_total", it["count"] / n * 100)

    # ── Step 11: redundancy — announcement chains on the same symbol-day ──
    scorable = A[~A["excluded_by_filter"]]
    sd = scorable.groupby(["symbol", "ann_day"])
    sizes = sd.size()
    multi_keys = set(sizes[sizes >= 2].index)
    in_multi = scorable.set_index(["symbol", "ann_day"]).index.isin(multi_keys)
    types_by_sd = sd["info_type"].apply(lambda s: sorted(set(s)))
    pairs = Counter()
    for ts in types_by_sd:
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                pairs[(ts[i], ts[j])] += 1
    results_days = {k for k, v in types_by_sd.items() if "financial_results" in v}
    echo_types = {"press_release", "investor_presentation", "analyst_meet_notice", "board_meeting_outcome"}
    echo = scorable[[((s, d) in results_days) and (t in echo_types)
                     for s, d, t in zip(scorable["symbol"], scorable["ann_day"], scorable["info_type"])]]
    same_type_extra = int((sd["info_type"].value_counts() - 1).clip(lower=0).sum())
    M["redundancy"] = {
        "scorable_records": int(len(scorable)),
        "symbol_days": int(len(sizes)),
        "symbol_days_with_2plus": int(len(multi_keys)),
        "records_in_multi_record_symbol_days": int(in_multi.sum()),
        "records_in_multi_record_symbol_days_%": pct(in_multi.sum(), len(scorable)),
        "same_symbol_day_same_type_extras": same_type_extra,
        "same_symbol_day_same_type_extras_%": pct(same_type_extra, len(scorable)),
        "results_days": len(results_days),
        "results_echo_records": int(len(echo)),
        "results_echo_by_type": echo["info_type"].value_counts().to_dict(),
        "top_type_pairs_same_symbol_day": [[a, b, c] for (a, b), c in pairs.most_common(12)],
    }
    it["same_symbol_day_extra_%"] = scorable.groupby("info_type").apply(
        lambda d: pct((d.groupby(["symbol", "ann_day"]).size() - 1).clip(lower=0).sum(), len(d))
    ).reindex(it.index).fillna(0)

    # ── PDF sample (content) ──
    P = pdf_sample(A, args.fetch_missing)
    if P is not None:
        P.to_csv(OUT / "pdf_sample_results.csv", index=False)
        ok = P[P["status"].isin(["ok", "unreadable"])]
        M["pdf_sample"] = {
            "n": int(len(P)), "missing_from_cache": int((P["status"] == "missing_from_cache").sum()),
            "status": P["status"].value_counts().to_dict(),
            "p1_cover_letter_%": pct(ok["p1_cover_letter"].sum(), len(ok)),
            "single_page_%": pct(ok["total_pages"].eq(1).sum(), len(ok)),
            "pages_median": float(ok["total_pages"].median()),
            "tok_first3_median": round(float(ok["tok_first3"].median())),
            "tok_first3_p90": round(float(ok["tok_first3"].quantile(.9))),
            "tok_whole_median": round(float(ok["tok_whole"].median())),
            "tok_whole_mean": round(float(ok["tok_whole"].mean())),
            "tok_whole_max": round(float(ok["tok_whole"].max())),
            "sha256_mismatch": int((P.get("sha256_match") == False).sum()),  # noqa: E712
        }
        pg = P.groupby("info_type")
        it["pdf_sample_n"] = pg.size().reindex(it.index).fillna(0).astype(int)
        it["pdf_unreadable_%"] = pg.apply(lambda d: pct(d["status"].eq("unreadable").sum(), len(d))).reindex(it.index)
        it["pdf_median_pages"] = pg["total_pages"].median().reindex(it.index)

    it = it.reset_index()
    it.to_csv(OUT / "data_value_analysis.csv", index=False)

    # ── Step 13: cost scenarios, on actual call volume ──
    base_calls = real_calls
    once = A[A["processed"]]
    scen = []
    def add(name, mask, note):
        calls = int(mask.sum())
        cost = float(A.loc[mask, "est_cost_once"].sum())
        scen.append({"scenario": name, "calls": calls,
                     "calls_reduction_%_vs_actual": pct(base_calls - calls, base_calls),
                     "est_cost_usd": round(cost, 2), "note": note})
    scen.append({"scenario": "A. actual (archived real calls)", "calls": base_calls,
                 "calls_reduction_%_vs_actual": 0.0, "est_cost_usd": M["ai"]["est_cost_actual_usd"],
                 "note": "includes the re-scoring loop"})
    add("B. each announcement scored once", A["processed"], "fixes the rebuild bug only")
    add("C. B minus duplicates", A["processed"] & ~A["dup_any"], "exact + near + cross-day re-filings")
    add("D. C minus label-only headlines", A["processed"] & ~A["dup_any"] & ~A["label_only"],
        "HEADLINE arm only: no company-specific input. NOT a PDF-arm rule")
    add("E. D restricted to PIT NIFTY500", A["processed"] & ~A["dup_any"] & ~A["label_only"] & A["pit_nifty500"],
        "a study-scope decision, not a data-quality one")
    SC = pd.DataFrame(scen)
    SC.to_csv(OUT / "cost_scenarios.csv", index=False)

    # ── Quality-problem union (record-level defects only; templating is not a defect) ──
    defect = pd.DataFrame({
        "empty_headline": A["empty_headline"],
        "missing_attachment": A["attachment_url"].isna(),          # the 08-08 file predates the column
        "non_pdf_attachment": A["url_ext"].notna() & ~A["url_ext"].eq("pdf"),
        "missing_isin": A["isin"].isna(),
        "missing_industry": A["industry"].isna() | A["industry"].astype(str).str.strip().eq(""),
        "timestamp_invalid_or_negative_lag": A["ann_ts"].isna() | (lag_dis < 0),
        "exact_duplicate": A["dup_exact"],
        "no_price_data_for_symbol": ~A["has_price_data"],
    })
    M["defects"] = {k: int(v.sum()) for k, v in defect.items()}
    core = defect[["empty_headline", "missing_attachment", "non_pdf_attachment", "missing_isin",
                   "timestamp_invalid_or_negative_lag", "exact_duplicate"]].any(axis=1)
    M["defects"]["any_core_defect"] = int(core.sum())
    M["defects"]["any_core_defect_%"] = pct(core.sum(), n)

    # ── Classification validation sample (read by a human; see report) ──
    val = A.sample(150, random_state=20260927)[["category", "info_type", "residual"]]
    val["residual"] = val["residual"].str.slice(0, 150)
    val.to_csv(OUT / "classification_validation_sample.csv", index=False)
    rv = A[A["signal"]].sample(60, random_state=7)[["category", "verdict", "no_info_signal", "rationale"]]
    rv.to_csv(OUT / "no_info_rationale_validation_sample.csv", index=False)

    # ── Evidence tiers (Steps 9-10). Defined by measured properties, reported
    #    as candidates for deprioritisation — nothing is excluded by this script.
    tier1 = A["excluded_by_filter"]
    procedural = ~tier1 & A["info_type"].isin(LOW_VALUE_TYPES)
    priority = ~tier1 & A["info_type"].isin(PRIORITY_TYPES)
    def tier(mask):
        return {"records": int(mask.sum()), "records_%": pct(mask.sum(), n),
                "actual_calls": int(A.loc[mask, "n_real_calls"].sum()),
                "actual_calls_%": pct(A.loc[mask, "n_real_calls"].sum(), real_calls),
                "calls_if_once": int((mask & A["processed"]).sum()),
                "signal_%": pct((mask & A["signal"]).sum(), (mask & A["processed"]).sum()),
                "no_info_signal_%": pct((mask & A["no_info_signal"]).sum(), (mask & A["signal"]).sum()),
                "label_only_%": pct((mask & A["label_only"]).sum(), mask.sum()),
                "pit_nifty500_%": pct((mask & A["pit_nifty500"]).sum(), mask.sum())}
    M["tiers"] = {"T1_already_excluded": tier(tier1),
                  "T2_procedural_types": tier(procedural),
                  "T2a_procedural_and_label_only": tier(procedural & A["label_only"]),
                  "T3_priority_types": tier(priority),
                  "T4_everything_else": tier(~tier1 & ~procedural & ~priority),
                  "low_value_types": sorted(LOW_VALUE_TYPES), "priority_types": sorted(PRIORITY_TYPES)}
    mask = A["processed"] & ~procedural
    SC = pd.concat([SC, pd.DataFrame([{
        "scenario": "C'. B minus T2 procedural types", "calls": int(mask.sum()),
        "calls_reduction_%_vs_actual": pct(base_calls - mask.sum(), base_calls),
        "est_cost_usd": round(float(A.loc[mask, "est_cost_once"].sum()), 2),
        "note": "candidate only; transcripts and AGM special business are exceptions"}])], ignore_index=True)
    SC.to_csv(OUT / "cost_scenarios.csv", index=False)
    (OUT / "cost_scenarios.md").write_text(md_table(SC, "{:,.2f}"))

    res = A[A["info_type"].eq("financial_results")]
    M["results_by_week"] = {str(k): {"all": int(v), "pit_nifty500": int(res.loc[res["ann_ts"].dt.to_period("W").astype(str).eq(k), "pit_nifty500"].sum())}
                            for k, v in res["ann_ts"].dt.to_period("W").astype(str).value_counts().sort_index().items()}

    keep = ["file_day", "seq_id", "symbol", "company", "category", "info_type", "announced_at",
            "disseminated_at", "ingested_at", "ingest_clock", "url_ext", "attachment_size", "residual",
            "label_only", "templated", "empty_headline", "has_amount", "excluded_by_filter",
            "dup_exact", "dup_near_same_day", "dup_cross_day", "dup_any", "repeat_text_different_doc",
            "project_filter_drop", "project_filter_drop_other_category", "pit_nifty500", "has_price_data",
            "processed", "n_real_calls", "n_empty_rows", "verdict", "signal", "no_info_signal", "flipped",
            "est_cost_actual"]
    A[keep].to_csv(OUT / "records_enriched.csv.gz", index=False)

    # ── Step 12: scorecard. Raw metrics plus a band from the fixed thresholds in
    #    SCORECARD_BANDS; deliberately NO composite score (see data_value_analysis.md).
    rows = []
    for name, d in A[~A["excluded_by_filter"]].groupby("category"):
        if len(d) < SCORECARD_MIN_RECORDS:
            continue
        has_doc = d["attachment_url"].fillna("").str.startswith("https://")
        by_design = d["attachment_url"].eq("-")
        resc = d["n_real_calls"].gt(1)
        sig = d["signal"].sum()
        m = {"category": name, "records": len(d), "info_type": d["info_type"].mode().iloc[0],
             "completeness_%": pct((has_doc | by_design).sum(), len(d)),
             "validity_%": pct((~d["empty_headline"] & d["ann_ts"].notna()).sum(), len(d)),
             "uniqueness_%": round(100 - pct(d["dup_any"].sum(), len(d)), 1),
             "specific_headline_%": pct((~d["templated"]).sum(), len(d)),
             "informative_signal_%": pct((d["signal"] & ~d["no_info_signal"]).sum(), d["processed"].sum()),
             "stability_%": round(100 - pct((d["flipped"] & resc).sum(), resc.sum()), 1) if resc.sum() >= 10 else None,
             "rescored_n": int(resc.sum())}
        for metric, (hi, lo) in SCORECARD_BANDS.items():
            v = m[metric]
            m[metric.replace("_%", "_band")] = ("n/a" if v is None else "High" if v >= hi else "Low" if v < lo else "Med")
        rows.append(m)
    SCD = pd.DataFrame(rows).sort_values("records", ascending=False)
    SCD.to_csv(OUT / "scorecard.csv", index=False)
    (OUT / "scorecard.md").write_text(md_table(SCD))

    (OUT / "audit_metrics.json").write_text(json.dumps(M, indent=2, default=str))
    (OUT / "info_type_table.md").write_text(md_table(it))
    (OUT / "category_table.md").write_text(md_table(cat))
    (OUT / "field_completeness.md").write_text(md_table(FC))
    (OUT / "cost_scenarios.md").write_text(md_table(SC, "{:,.2f}"))
    print(json.dumps(M, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
