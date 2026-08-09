"""The prompt must be the paper's, transcribed — not reconstructed.

A from-memory paraphrase would look plausible, hash cleanly, and quietly stop
this being a replication. These tests fail while the placeholder stands, so the
transcription cannot be forgotten and scoring cannot start without it.

Transcribed 2026-08-10 from the paper's Section 5, page 5. These tests now pin
that text: they fail if anyone edits the prompt without meaning to, and the
hash check makes an accidental edit visible as a changed arm rather than as
silently mixed data.
"""
from __future__ import annotations

import pytest

from catalan.scoring import prompt


def test_prompt_is_transcribed_before_any_scoring_run():
    assert prompt.PROMPT_TEMPLATE != prompt.PLACEHOLDER, (
        "Transcribe the prompt verbatim from Lopez-Lira & Tang (JFE 184, 2026), "
        "PDF at ~/Desktop/Proyectos/Scilian-Books/Catalan/, then set "
        "PROMPT_FROZEN = True. Do not reconstruct it from memory."
    )
    assert prompt.PROMPT_FROZEN


def test_render_refuses_to_run_against_the_placeholder():
    if prompt.PROMPT_FROZEN and prompt.PROMPT_TEMPLATE != prompt.PLACEHOLDER:
        pytest.skip("prompt has been frozen — guard no longer applicable")
    with pytest.raises(RuntimeError, match="not been transcribed"):
        prompt.render("Reliance Industries Limited", "Board approves demerger")


def test_prompt_hash_is_stable_and_content_addressed():
    assert prompt.prompt_hash("abc") == prompt.prompt_hash("abc")
    assert prompt.prompt_hash("abc") != prompt.prompt_hash("abd")
    assert len(prompt.prompt_hash("abc")) == 16


def test_placeholders_survive_into_render_contract():
    """The transcribed text must keep the paper's substitution slots."""
    assert "_company_name_" in prompt.PROMPT_TEMPLATE
    assert "_headline_" in prompt.PROMPT_TEMPLATE


def test_transcription_matches_the_paper():
    """Pins the load-bearing phrases from Section 5, page 5.

    Not a full string equality check — that would just restate the constant.
    These are the clauses that would change the instrument if they drifted.
    """
    t = prompt.PROMPT_TEMPLATE
    assert t.startswith("Forget all your previous instructions.")
    assert "Pretend you are a financial expert." in t
    assert "stock recommendation experience" in t
    assert '"YES" if good news, "NO" if bad news, or "UNKNOWN" if uncertain' in t
    assert "in the first line" in t
    assert "one short and concise sentence on the next line" in t
    assert t.endswith("Headline: _headline_")


def test_paper_hardcodes_short_term_rather_than_a_placeholder():
    """The printed prompt says 'in the short term' — there is no _term_ slot,
    so CATALAN must not invent one."""
    assert "in the short term" in prompt.PROMPT_TEMPLATE
    assert "_term_" not in prompt.PROMPT_TEMPLATE


def test_render_substitutes_both_slots_and_leaves_nothing_behind():
    out = prompt.render("Reliance Industries Limited", "Board approves demerger")
    assert "Reliance Industries Limited" in out
    assert "Board approves demerger" in out
    assert "_company_name_" not in out
    assert "_headline_" not in out


def test_system_prefix_is_empty_by_design():
    """The paper's prompt opens with 'Forget all your previous instructions';
    a system persona would change the instrument."""
    assert prompt.SYSTEM_PREFIX == ""
