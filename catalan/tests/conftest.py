"""Test config.

CATALAN imports two things from the main repo (quant_engine.data.membership and
quant_engine.data.turso_client), so the repo root must be importable. Adding it
here keeps `python3 -m pytest catalan/tests/` working from any cwd.

Every test in this suite runs offline: no Turso, no NSE, no Anthropic.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
