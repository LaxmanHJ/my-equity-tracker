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
    assert hashes == {EXPECTED_PROMPT_HASH}, (
        f"the local store holds scores under prompt hashes {sorted(hashes)}, but "
        f"only {EXPECTED_PROMPT_HASH} is declared. Either an undeclared arm was "
        "run, or the pin above was updated without declaring the new arm."
    )


# ── G2: the frozen parameter set ──────────────────────────────────────────────
# Every constant here decides which rows enter C0/C1/C2 or what a return means.
# Deliberately NOT auto-derived from config.__dict__: that would silently absorb
# any new constant, which is the failure this gate exists to catch.
FROZEN = {
    "STUDY_VERSION": "catalan-v1",
    "UNIVERSE_INDEX": "NIFTY500",
    # Session clock
    "MARKET_CLOSE": "15:30:00",
    "NEWS_CUTOFF": "09:00:00",
    "MARKET_OPEN": "09:15:00",
    # Corpus
    "HISTORY_START": "2019-01-01",
    "DEDUP_SIMILARITY": 0.6,
    # Scoring
    "SCORE_VALUES": {"YES": 1, "UNKNOWN": 0, "NO": -1},
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
}

EXPECTED_PARAM_HASH = "6abcc018abcce2fa"


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
                   "EXCHANGE_TXN_", "STAMP_DUTY_", "SEBI_", "GST_")

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
