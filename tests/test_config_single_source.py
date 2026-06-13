"""CI guard: runtime config has a single source of truth (issue #943).

Before #943, ``RUNNER_BASE_DIR``/``ORG``/``HOSTNAME``/runner limits were
re-derived from ``os.environ`` independently in ``runners/service_control.py``
and ``routers/system.py``, so an operator's ``RUNNER_BASE_DIR`` override was
honored for metrics but ignored by the sudo-executed svc.sh path. These tests
fail if that divergence is re-introduced, and if more than one ``runner_limit``
definition reappears.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"

# Files that previously re-derived config and must now read dashboard_config.
# dashboard_config/ itself is the source of truth and is exempt.
_SINGLE_SOURCE_FILES = [
    BACKEND_DIR / "runners" / "service_control.py",
    BACKEND_DIR / "routers" / "system.py",
]

# Config names that must come from dashboard_config, never re-derived locally.
_GUARDED_ENV_VARS = [
    "RUNNER_BASE_DIR",
    "GITHUB_ORG",
    "NUM_RUNNERS",
    "MAX_RUNNERS",
    "DISPLAY_NAME",
    "RUNNER_ALIASES",
]


def test_guarded_files_do_not_re_derive_config_from_environ() -> None:
    """The previously-divergent modules must not read guarded vars from os.environ."""
    violations: list[str] = []
    for py_file in _SINGLE_SOURCE_FILES:
        text = py_file.read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for var in _GUARDED_ENV_VARS:
                # Match os.environ reads of a guarded var (getenv or subscript).
                if re.search(rf'os\.environ(?:\.get)?\(\s*["\']{var}["\']', line) or re.search(
                    rf'os\.getenv\(\s*["\']{var}["\']', line
                ):
                    violations.append(f"{py_file}:{lineno}: {stripped}")
    assert not violations, "config re-derived from os.environ instead of dashboard_config (issue #943):\n" + "\n".join(
        violations
    )


def test_exactly_one_runner_limit_definition_repo_wide() -> None:
    """``runner_limit`` / ``_runner_limit`` must be defined exactly once (#943)."""
    pattern = re.compile(r"^def (?:_)?runner_limit\b", re.MULTILINE)
    definitions: list[str] = []
    for py_file in BACKEND_DIR.rglob("*.py"):
        text = py_file.read_text(errors="replace")
        for match in pattern.finditer(text):
            lineno = text[: match.start()].count("\n") + 1
            definitions.append(f"{py_file}:{lineno}: {match.group(0)}")
    assert len(definitions) == 1, "expected exactly one runner_limit definition, found:\n" + "\n".join(definitions)
    assert "dashboard_config" in definitions[0], (
        f"the single runner_limit definition must live in dashboard_config, found: {definitions[0]}"
    )
