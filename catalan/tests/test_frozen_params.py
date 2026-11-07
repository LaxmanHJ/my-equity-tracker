"""Gates G2 and G3 — the study parameters and the instrument cannot drift silently.

``catalan/CLAUDE.md``: *"Config constants in `config.py` are frozen study
parameters. Changing one after scoring begins invalidates the run."*

Until now that was a sentence. These tests are what turn it into something that
stops a commit.

**Three checks, doing three different jobs.** The per-constant assertions say
*what each value must be*, so a failing diff explains itself. The set hash
catches an addition to or removal from the declared list, which no individual
assertion can see — a study whose parameter list grew after scoring began is
exactly as invalid as one whose values changed. And the prefix sweep catches the
one both would miss: a NEW study parameter added to `config.py` that nobody
remembered to declare here, which would otherwise leave this gate quietly
partial while still reporting green.

**Updating these pins is not a chore, it is the decision point.** If you are
here because a test failed, the question is not "what is the new hash" — it is
whether the run is still the run PREREGISTRATION.md describes. If scoring has
begun, the answer is no: bump STUDY_VERSION and treat it as a separate study.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from catalan import config
from catalan.scoring import prompt

# ── G3: the instrument ────────────────────────────────────────────────────────
# The prompt IS the measurement instrument. Silent drift would change what the
# study measures mid-run and leave no trace: score rows carry the prompt hash,
# so mixed-prompt data would look like one clean arm rather than two.
EXPECTED_PROMPT_HASH = "1cc1a88738863a44"
# The PDF arm (2026-09 amendment): the paper's prompt with "headline" →
# "announcement" and the first PDF pages appended. See prompt.PDF_PROMPT_TEMPLATE.
EXPECTED_PDF_PROMPT_HASH = "55503e79581ae655"
DECLARED_ARM_HASHES = {EXPECTED_PROMPT_HASH, EXPECTED_PDF_PROMPT_HASH}


def test_prompt_hash_is_pinned_to_the_transcribed_paper_text():
    got = prompt.prompt_hash(prompt.PROMPT_TEMPLATE)
    assert got == EXPECTED_PROMPT_HASH, (
        f"the scoring prompt now hashes to {got}, not {EXPECTED_PROMPT_HASH}. "
        "The prompt is the study's instrument — it was transcribed verbatim "
        "from Lopez-Lira & Tang (JFE 184, 2026) Section 5, p.5 on 2026-08-10 "
        "and every score row already collected is stamped with the old hash. "
        "If the change is deliberate, it is a NEW ARM: leave the old rows "
        "alone (scores are append-only) and record the new hash here."
    )


def test_pdf_arm_prompt_hash_is_pinned():
    got = prompt.prompt_hash(prompt.PDF_PROMPT_TEMPLATE)
    assert got == EXPECTED_PDF_PROMPT_HASH, (
        f"the PDF-arm prompt now hashes to {got}, not {EXPECTED_PDF_PROMPT_HASH}. "
        "It is the PDF arm's instrument, declared in the 2026-09 amendment. A "
        "change is a new arm: declare it, never overwrite this pin to go green."
    )


def test_scored_rows_in_the_archive_carry_the_pinned_hash():
    """Whatever has already been scored must match the pin, or the pin is wrong.

    Catches the reverse mistake: someone updates EXPECTED_PROMPT_HASH to make
    the test pass, without noticing that the corpus was scored under the old one.
    """
    from catalan.data.store import store

    conn = store()
    try:
        rows = conn.execute(
            "SELECT DISTINCT prompt_hash FROM scores").fetchall()
    except Exception:                       # no local store in a fresh checkout
        pytest.skip("no local catalan.db to check")
    finally:
        conn.close()

    hashes = {r[0] for r in rows}
    if not hashes:
        pytest.skip("nothing scored yet in this checkout")
    assert hashes <= DECLARED_ARM_HASHES, (
        f"the local store holds scores under prompt hashes {sorted(hashes)}, but "
        f"only {sorted(DECLARED_ARM_HASHES)} are declared. Either an undeclared arm "
        "was run, or a pin above was updated without declaring the new arm."
    )


# ── G2: the frozen parameter set ──────────────────────────────────────────────
# Every constant here decides which rows enter C0/C1/C2 or what a return means.
# Deliberately NOT auto-derived from config.__dict__: that would silently absorb
# any new constant, which is the failure this gate exists to catch.
FROZEN = {
    # v2: the 2026-09 amendment (long-only, PDF arm, dedup rule, temperature).
    # v1's forward log (2026-08-07 on) is the declared pilot.
    "STUDY_VERSION": "catalan-v2",
    "STUDY_CLOCK_START": "2026-10-26",
    # The instrument's model and the study universe (Amendment 1, 2026-10-18).
    "PRIMARY_MODEL": "gemini-3.5-flash-lite",
    "UNIVERSE_FILE": "data/universe/nifty500_2026-10-18.csv",
    "UNIVERSE_FILE_SHA256": "2959bf206239284e145f7aecc65095b18d11556d2642323a70f2d26efe0f5cb3",
    "UNIVERSE_SERIES": ["EQ"],
    "UNIVERSE_EXCLUDED": ["DUMMYHEG", "TRENT", "CUB", "HEGAM"],
    "UNIVERSE_INDEX": "NIFTY500",
    # Session clock
    "MARKET_CLOSE": "15:30:00",
    "NEWS_CUTOFF": "09:00:00",
    "MARKET_OPEN": "09:15:00",
    # Corpus
    "HISTORY_START": "2019-01-01",
    "DEDUP_SIMILARITY": 1.0,
    # Scoring
    "SCORE_VALUES": {"YES": 1, "UNKNOWN": 0, "NO": -1},
    "TEMPERATURE": 0.0,
    "TEMPERATURE_MODEL_PREFIXES": ["claude-haiku-4-5", "gemini-3.5-flash-lite"],
    # Liquidity
    "LIQUIDITY_BUCKETS": 5,
    "ADV_WINDOW_DAYS": 20,
    "ADV_MEASURE": "turnover_inr",
    "LIQUIDITY_BUCKET_1_IS_LEAST_LIQUID": True,
    # Panel construction
    "PANEL_CA_GAP_LIMIT": 0.20,
    "PANEL_MIN_SESSION_BREADTH_ABS": 20,
    "PANEL_MIN_SESSION_BREADTH_REL": 0.25,
    "PANEL_CADENCE_WINDOW": 10,
    "PANEL_CADENCE_MIN_PERIODS": 5,
    "PANEL_CADENCE_MAX_MEDIAN_GAP": 1.6,
    "PANEL_WARMUP_SESSIONS": 30,
    # Cost model — declared before the result was seen, which is the whole point
    "BROKERAGE_PER_ORDER_CAP": 20.0,
    "BROKERAGE_RATE": 0.0003,
    "STT_SELL_RATE": 0.00025,
    "EXCHANGE_TXN_RATE": 0.0000297,
    "STAMP_DUTY_BUY_RATE": 0.00003,
    "SEBI_TURNOVER_RATE": 0.000001,
    "GST_RATE": 0.18,
    "REBALANCE_FRACTIONS": [1.0, 0.25],
    "HEDGE_COST_BPS": 1.0,
    "POSITION_NOTIONAL_INR": 100_000,
    "USABLE_SESSION_MIN_COVERAGE": 0.80,
    "SPREAD_ESTIMATOR": "abdi_ranaldo_2017",
    "SPREAD_WINDOW_SESSIONS": 20,
    "SPREAD_MIN_PAIRS": 15,
    # Frozen by Amendment 1 (catalan-v2); open item 3 closed.
    "CATEGORY_EXCLUSIONS_FROZEN": True,
    "CATEGORY_EXCLUSIONS": ["Trading Window", "Copy of Newspaper Publication",
                            "Disclosure under SEBI Takeover Regulations"],
    "HEADLINE_EXCLUSIONS": ["copy of newspaper publication"],
    # PDF arm — which filings carry attachment text, and how much of it
    "PDF_PAGE_WINDOW": 3,
    "PDF_MIN_PAGE_CHARS": 50,
    "PDF_GARBLE_MAX": 0.15,
    "PDF_GARBLE_ALLOWED": "\u0900-\u097F₹–—’‘“”•●▪…©®°±×",
    "PDF_MAX_CHARS": 30_000,
}

EXPECTED_PARAM_HASH = "20fbff34ee4da733"   # catalan-v2 amendment set, 2026-10-18


def _actual(name: str):
    """config's value for `name`, normalised to something JSON can hold."""
    value = getattr(config, name)
    if hasattr(value, "isoformat"):          # datetime.time
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _param_hash(values: dict) -> str:
    blob = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@pytest.mark.parametrize("name,expected", sorted(FROZEN.items()))
def test_frozen_parameter_still_holds_its_declared_value(name, expected):
    assert _actual(name) == expected, (
        f"frozen study parameter {name} is {_actual(name)!r}, declared as "
        f"{expected!r} in PREREGISTRATION.md. Changing a frozen parameter after "
        "scoring begins INVALIDATES THE RUN — the pre-registered thresholds were "
        "set against these numbers. If the change is genuinely necessary, bump "
        "STUDY_VERSION and treat the result as a separate study."
    )


def test_the_frozen_parameter_SET_has_not_grown_or_shrunk():
    """The check the per-parameter assertions cannot make.

    A study whose declared parameter list changed after scoring began is exactly
    as invalid as one whose values changed, and only a hash over the whole set
    notices an addition or a removal.
    """
    got = _param_hash({k: _actual(k) for k in FROZEN})
    assert got == EXPECTED_PARAM_HASH, (
        f"the frozen parameter set now hashes to {got}, not {EXPECTED_PARAM_HASH}. "
        "A declared study parameter was added, removed or changed. Update "
        "PREREGISTRATION.md's 'Frozen study parameters' table in the same commit, "
        "and if scoring has already begun, bump STUDY_VERSION instead."
    )


# Any config constant beginning with one of these is a measurement rule by
# construction, so it must be declared frozen. Without this sweep the gate is
# only as complete as whoever last remembered to update FROZEN.
FROZEN_PREFIXES = ("PANEL_", "ADV_", "LIQUIDITY_", "BROKERAGE_", "STT_",
                   "EXCHANGE_TXN_", "STAMP_DUTY_", "SEBI_", "GST_", "PDF_",
                   "DEDUP_", "TEMPERATURE", "STUDY_", "HEDGE_", "CATEGORY_", "HEADLINE_",
                   "UNIVERSE_", "POSITION_", "SPREAD_", "PRIMARY_", "USABLE_")

# Declared exemptions, each with a reason. An exemption is a claim that the
# constant does not change what gets measured — not a way to silence the gate.
FROZEN_PREFIX_EXEMPT = {
    # A transport hint (Turso's 30s timeout), not a measurement rule: chunking
    # the same date range differently returns the same rows.
    "PANEL_FETCH_CHUNK",
}


def test_no_undeclared_study_parameter_slipped_into_config():
    """A new PANEL_* / cost constant must be declared, not just written.

    This is the check that keeps the gate honest over time. FROZEN is hand-kept
    on purpose — auto-deriving it from config.__dict__ would silently absorb
    every new constant, which is the exact failure being guarded against — so
    something has to notice when config grows and FROZEN does not.
    """
    candidates = {
        name for name in dir(config)
        if name.isupper() and name.startswith(FROZEN_PREFIXES)
    }
    undeclared = sorted(candidates - set(FROZEN) - FROZEN_PREFIX_EXEMPT)
    assert not undeclared, (
        f"config.py defines {undeclared} but PREREGISTRATION.md and this gate do "
        "not declare them. Every panel/liquidity/cost constant is a measurement "
        "rule: add it to FROZEN and to the pre-registration's frozen-parameter "
        "table, or add it to FROZEN_PREFIX_EXEMPT with a reason why it cannot "
        "change what is measured."
    )


def test_every_frozen_parameter_actually_exists_in_config():
    """A typo in FROZEN would otherwise make this gate silently partial."""
    missing = [k for k in FROZEN if not hasattr(config, k)]
    assert not missing, f"declared frozen but absent from config.py: {missing}"


def test_preregistration_documents_the_panel_rules_it_claims_to_freeze():
    """The config and the pre-registration must not disagree.

    PREREGISTRATION.md is the authority a reader checks; config.py is what the
    code obeys. If a number lives in only one of them, the study's integrity
    argument is decoration.
    """
    from catalan.config import CATALAN_ROOT

    text = (CATALAN_ROOT / "PREREGISTRATION.md").read_text()
    for needle in ("0.20", "rupee turnover", "1 = LEAST liquid",
                   "No forward-fill", "1.6"):
        assert needle in text, (
            f"PREREGISTRATION.md does not mention {needle!r}; the panel rule it "
            "corresponds to is frozen in config.py but undeclared to the reader."
        )


# ── G2b: the gate thresholds are frozen too (set 2026-08-20) ──────────────────
#
# The thresholds live in PREREGISTRATION.md rather than config.py, because that
# document is their authority. That means the hash gate above does not cover
# them — so they get their own pin. A threshold that can be edited after a
# result is seen is not a pre-registered threshold.

GATE_THRESHOLDS = [
    # C0 — comprehension smoke test
    "daily long-short hit rate ≥ 70%",
    "Minimum observations: 40 trading days",
    # C1 — gross drift
    "mean daily long-short drift > 0 with t ≥ 2.0",
    # C2 — the ship gate, all four conditions
    "mean daily net LS drift **> 0 with t ≥ 2.0**",
    "mean daily net LS drift **≥ 5 bps**",
    "net annualized Sharpe ≥ 0.5",
    "Deflated Sharpe > 0.95",
    # Multiple testing + horizon
    "**Declared cell count for the deflation: K = 5.**",
    "evaluated exactly once, at 120 trading days",
]


@pytest.mark.parametrize("clause", GATE_THRESHOLDS)
def test_gate_threshold_is_still_declared(clause):
    from catalan.config import CATALAN_ROOT

    text = (CATALAN_ROOT / "PREREGISTRATION.md").read_text()
    assert clause in text, (
        f"PREREGISTRATION.md no longer declares {clause!r}. Gate thresholds were "
        "fixed on 2026-08-20, blind to every quantity they judge (no scored "
        "headline had a return joined to it — price_history ended before the "
        "forward log began). Editing one after a gate statistic becomes "
        "computable voids the pre-registration. If a threshold genuinely must "
        "change, bump STUDY_VERSION and start a separate study."
    )


def test_no_TBD_thresholds_remain():
    """A `TBD` in the gates section means the study cannot legitimately report."""
    from catalan.config import CATALAN_ROOT

    text = (CATALAN_ROOT / "PREREGISTRATION.md").read_text()
    gates = text.split("## Gates", 1)[1].split("## Cost model", 1)[0]
    assert "TBD" not in gates, "an unset threshold is still marked TBD in the gates section"


def test_the_stopping_rule_is_declared():
    """Optional stopping is the failure a pre-registration exists to prevent.

    Without a fixed horizon and a declared 'inconclusive' outcome, the natural
    behaviour — collect, re-check, stop when t crosses 2 — inflates the
    false-positive rate and leaves no trace in the reported numbers.
    """
    from catalan.config import CATALAN_ROOT

    text = (CATALAN_ROOT / "PREREGISTRATION.md").read_text()
    for needle in ("There is no second look and no extension",
                   "underpowered — no result",
                   "A near-miss is a miss"):
        assert needle in text, f"the stopping rule no longer states: {needle!r}"


# ── G2 extension: the CODE that implements frozen v2 rules (verifier, 2026-10-18)
# The values above pin thresholds; these pin behaviour. A change to the filer /
# stem stripping (dedup) or to the extracted-text format (the PDF arm's input)
# changes what is measured even when no constant moves.

GOLDEN_HEADLINES = [
    "Centum Electronics Limited has informed the Exchange about Schedule of meet",
    "The Bombay Burmah Trading Corporation Limited has submitted the Exchange a copy Srutinizers report of  Annual General Meeting held on August 13, 2026. Further, the company has informed the Exchange regarding voting results.",
    "TGB Banquets And Hotels Limited has informed the Exchange about General Updates",
    "TARC Limited has informed the Exchange regarding Outcome of Board Meeting held on August 11, 2026.",
    "Arrow Greentech Limited has informed the Exchange regarding 'Presentation on Business Performance'.",
    "Laxmi Dental Limited has informed the Exchange regarding a press release dated August 11, 2026, titled \"Intimation of Press Release\".",
    "Pursuant to Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015, Bata India Limited has informed about changes in SMP",
    "Vedant Fashions Limited has informed the Exchange that Record date for the purpose of Dividend  is 21-Sep-2026.",
    "Re-appointment of Mr. Nikhil Khandelwal as a Managing Director of the Company for a further period of three years w.e.f September 01, 2026, subject to the approval of the members in the ensuing AGM.",
    "JSW Steel Limited has informed the Exchange about Resignation of Director/KMP/SMP",
    "Adani Power Limited has informed the Exchange about Credit Rating",
    "Zen Technologies Limited has informed the Exchange regarding Exercise of 1900  Options.",
    "Disclosure under Regulation 32 of the SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015 - Monitoring Agency Report for the quarter ended June 30, 2026.",
    "Texmaco Rail & Engineering Limited has informed the Exchange about receipt of Order worth Rs. 27.82 Crores (incl. taxes) from Hindalco Industries Limited",
    "Neogen Chemicals Limited has informed the Exchange regarding allotment of 2660753 securities pursuant to Qualified Institution Placement at its meeting held on September 16, 2026",
    "Significant increase in volume has been observed in Gandhi Special Tubes Limited. The Exchange, in order to ensure that investors have latest relevant information about the company and to inform the market place so that the interest of the investors is safeguarded, had written to the company. Gandhi Special Tubes Limited has submitted their response.",
    "PNB GILTS LTD. has informed the Exchange regarding the Trading Window closure pursuant to SEBI (Prohibition of Insider Trading) Regulations, 2015",
    "Chemcon Speciality Chemicals Limited has informed the Exchange regarding ''.",
    "Board approves demerger of financial services business",
]
EXPECTED_FILING_TEXT_HASH = "2ba995f82b5774f8"
EXPECTED_EXTRACT_HASH = "9643356483b5f040"


def _filing_text_hash() -> str:
    from catalan.scoring.filters import filing_text
    return hashlib.sha256("\n".join(filing_text(h) for h in GOLDEN_HEADLINES).encode()).hexdigest()[:16]


def _extract_hash() -> str:
    import fitz
    from catalan.data.pdf_text import extract
    doc = fitz.open()
    for body in ("Covering letter. Dear Sir, pursuant to Regulation 30 we enclose the results. " * 3,
                 "Revenue grew 23.5% to Rs 2,828 crore; EBITDA margin rose 140 bps. " * 3,
                 "Board approved a dividend of Rs 5 per share; record date 21-Sep-2026. " * 3,
                 "PAGE FOUR MUST NOT APPEAR " * 5):
        doc.new_page().insert_textbox(fitz.Rect(40, 40, 560, 800), body, fontsize=9)
    res = extract(doc.tobytes())
    doc.close()
    blob = json.dumps({k: res[k] for k in ("status", "total_pages", "page_kinds", "text", "truncated")},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def test_dedup_filing_text_behaviour_is_pinned():
    got = _filing_text_hash()
    assert got == EXPECTED_FILING_TEXT_HASH, (
        f"filters.filing_text now produces {got}, not {EXPECTED_FILING_TEXT_HASH}. The v2 dedup "
        "rule compares filing text, so this changes which filings enter the corpus: a frozen-"
        "parameter change under Amendment 1. Do not re-pin without declaring it.")


def test_pdf_extraction_output_is_pinned():
    got = _extract_hash()
    assert got == EXPECTED_EXTRACT_HASH, (
        f"pdf_text.extract now produces {got}, not {EXPECTED_EXTRACT_HASH}. Its text is the PDF "
        "arm's input: a change here (window, page markers, whitespace, readability rules, or a "
        "PyMuPDF upgrade) changes the instrument. Do not re-pin without declaring it.")
