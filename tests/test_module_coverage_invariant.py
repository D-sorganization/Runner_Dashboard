"""CI gate: every backend module must have a corresponding test file.

Implements the acceptance criterion from issue #386:
  > a tiny script in quality-gate walks backend/*.py and asserts a matching
  > tests/test_<name>.py exists for every module.

Modules in EXEMPT are intentionally excluded.
"""

from __future__ import annotations

from pathlib import Path

# Modules that are intentionally exempt from the "must have a test file" rule:
#   server.py   — the monolithic FastAPI app; integration-tested via test_api_integration.py
#   __init__.py — not a module with public API
EXEMPT: frozenset[str] = frozenset({"__init__", "server"})

REPO_ROOT = Path(__file__).parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
TESTS_DIR = REPO_ROOT / "tests"


def test_every_backend_module_has_a_test_file() -> None:
    """Every backend/*.py (minus EXEMPT) must have a tests/test_<name>.py."""
    missing: list[str] = []
    for p in sorted(BACKEND_DIR.glob("*.py")):
        if p.stem in EXEMPT:
            continue
        # Accept both tests/test_<stem>.py and tests/**/test_<stem>.py
        if not (TESTS_DIR / f"test_{p.stem}.py").exists() and not list(
            TESTS_DIR.glob(f"**/test_{p.stem}.py")
        ):
            missing.append(p.stem)

    assert not missing, (
        "The following backend modules have no test file — add tests/test_<module>.py for each:\n"
        + "\n".join(f"  backend/{m}.py" for m in sorted(missing))
    )
