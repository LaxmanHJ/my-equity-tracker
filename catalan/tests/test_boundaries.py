"""Gate G1 — the dependency arrow points one way only.

``catalan/CLAUDE.md``: *"Nothing under `catalan/` may be imported by `src/` or
`quant_engine/`. Deleting `catalan/` must never break the main app."*

That was a sentence in a document and nothing enforced it. This is what makes
it fail a commit instead. The direction matters because CATALAN reads the main
project's DATA and borrows none of its conclusions; an import in the other
direction would make the main app depend on a pre-registered study's internals
and turn "delete the study" into a breaking change.

An AST walk rather than a grep: a grep for "catalan" matches comments, strings
and the word "catalán", and would have to be muzzled with exceptions until it
meant nothing.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDED_TREES = ("src", "quant_engine")


def _python_files(tree: Path):
    for path in sorted(tree.rglob("*.py")):
        parts = set(path.parts)
        if parts & {"node_modules", ".venv", "venv", "__pycache__", "build", "dist"}:
            continue
        yield path


def _imports_catalan(path: Path) -> bool:
    """Does this module import `catalan` at any level?"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        # Not importable Python — it cannot create the dependency either way.
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "catalan" or a.name.startswith("catalan.")
                   for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "catalan" or mod.startswith("catalan."):
                return True
    return False


@pytest.mark.parametrize("tree_name", GUARDED_TREES)
def test_main_app_never_imports_catalan(tree_name):
    tree = REPO_ROOT / tree_name
    if not tree.is_dir():
        pytest.skip(f"{tree_name}/ does not exist in this checkout")

    offenders = [str(p.relative_to(REPO_ROOT))
                 for p in _python_files(tree) if _imports_catalan(p)]

    assert not offenders, (
        f"{tree_name}/ imports catalan: {offenders}. The dependency arrow points "
        "one way only — CATALAN may read the main project's data, but deleting "
        "catalan/ must never break the main app. Move the shared piece into "
        "quant_engine/ and have CATALAN import it from there."
    )


def test_the_guard_can_actually_see_an_import(tmp_path):
    """A gate that cannot fail is not a gate.

    Pins the detector itself against the three import spellings that matter, so
    a future refactor of _imports_catalan cannot quietly turn G1 into a no-op
    that passes forever.
    """
    cases = {
        "import catalan": True,
        "import catalan.data.panel": True,
        "from catalan.data import panel": True,
        "from catalan import config": True,
        "import quant_engine.data.membership": False,
        "# import catalan in a comment": False,
        "x = 'catalan'": False,
    }
    for source, expected in cases.items():
        f = tmp_path / "probe.py"
        f.write_text(source)
        assert _imports_catalan(f) is expected, source


def test_catalan_is_reachable_as_a_package():
    """The inverse direction is expected and must keep working."""
    from catalan.data import store, universe  # noqa: F401
