"""
The frozen prompt, and the machinery that keeps it frozen.

The prompt IS the instrument. If it drifts between runs, cross-arm comparisons
and the contamination test both become meaningless, so every score row is
stamped with ``prompt_hash`` and re-scoring under a changed prompt writes new
rows rather than overwriting.

TRANSCRIPTION PROVENANCE
------------------------
Transcribed 2026-08-10 from Lopez-Lira & Tang, "Can ChatGPT forecast stock price
movements? Return predictability and large language models", JFE 184 (2026)
104335, **Section 5 "ChatGPT prompt", page 5** of
``~/Desktop/Proyectos/Scilian-Books/Catalan/1-s2.0-S0304405X26001066-main.pdf``.

Two typographic normalisations were applied and nothing else:
  - the paper's typeset ``''YES''`` open/close quote pairs → plain ASCII "YES"
  - the line-break hyphenation ``financial ex-\\npert`` → ``financial expert``

Note the printed prompt hard-codes "in the short term" — it does NOT carry a
``_term_`` placeholder, so CATALAN does not add one. Only ``_company_name_`` and
``_headline_`` are substituted, exactly as in the paper.
"""
from __future__ import annotations

import hashlib

PROMPT_FROZEN = True

PLACEHOLDER = "TRANSCRIBE_FROM_PDF"

PROMPT_TEMPLATE = (
    'Forget all your previous instructions. Pretend you are a financial expert. '
    'You are a financial expert with stock recommendation experience. '
    'Answer "YES" if good news, "NO" if bad news, or "UNKNOWN" if uncertain in '
    'the first line. Then elaborate with one short and concise sentence on the '
    'next line. Is this headline good or bad for the stock price of '
    '_company_name_ in the short term?\n'
    'Headline: _headline_'
)

# Sent as the cached system prefix. It is deliberately EMPTY: the paper's prompt
# opens with "Forget all your previous instructions", and prepending a system
# persona would change the instrument. Prompt caching therefore buys nothing
# here, and that is the correct trade — fidelity over a few cents.
SYSTEM_PREFIX = ""

# Two deliberate engineering deviations from the paper, both recorded here
# because they change the instrument and must be reported as such:
#
# 1. The paper set temperature=0 for reproducibility. Opus 5 rejects
#    `temperature` with a 400, so determinism instead comes from pinning
#    (model_id, prompt_hash, effort) and recording all three on every row.
#
# 2. The paper parsed the first line of free text. Structured outputs
#    (output_config.format + JSON schema) are more robust, but they change the
#    elicitation — so A/B them against verbatim free-text parsing on 1,000
#    headlines and report agreement BEFORE adopting them as the primary path.
PARSE_MODE_STRUCTURED = "structured"
PARSE_MODE_FREETEXT = "freetext"
AB_SAMPLE_SIZE = 1000

# The paper's answer space, in first-line position.
ANSWERS = ("YES", "NO", "UNKNOWN")


def prompt_hash(template: str = None) -> str:
    """Stable identity for a prompt text. Stamped on every score row."""
    text = PROMPT_TEMPLATE if template is None else template
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def render(company_name: str, headline: str) -> str:
    """Fill the frozen template. Refuses to run while the placeholder stands."""
    if not PROMPT_FROZEN or PROMPT_TEMPLATE == PLACEHOLDER:
        raise RuntimeError(
            "CATALAN's prompt has not been transcribed from the paper yet. "
            "Scoring against a placeholder or a from-memory paraphrase would "
            "silently stop this being a replication. See catalan/scoring/prompt.py."
        )
    return (
        PROMPT_TEMPLATE
        .replace("_company_name_", company_name)
        .replace("_headline_", headline)
    )
