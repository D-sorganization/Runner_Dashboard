"""Tests for `deploy/setup.sh` hardening (issue #402).

These tests assert structural properties of the setup script:
- valid bash syntax (no execution),
- presence of the `preflight()` shell function,
- presence of `--check-only` and `--dry-run` argument modes,
- atomic sudoers replacement using `visudo -c -f`,
- version-skip restart logic that consults `git_sha`.

The script must remain under 500 lines (CI constraint) and free of
`TODO`/`FIXME` markers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_SH = REPO_ROOT / "deploy" / "setup.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SETUP_SH.is_file(), f"deploy/setup.sh not found at {SETUP_SH}"
    return SETUP_SH.read_text()


def test_setup_sh_syntax_check() -> None:
    """`bash -n deploy/setup.sh` must exit 0 (syntax-clean)."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available on this system")
    result = subprocess.run(
        [bash, "-n", str(SETUP_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_setup_sh_has_preflight_function(script_text: str) -> None:
    """A `preflight()` shell function must be defined."""
    assert "preflight()" in script_text, "preflight() function missing"


def test_setup_sh_has_check_only_flag(script_text: str) -> None:
    """`--check-only` mode must be present at argument parsing."""
    assert "--check-only" in script_text, "--check-only flag missing"


def test_setup_sh_has_dry_run_flag(script_text: str) -> None:
    """`--dry-run` mode must be present at argument parsing."""
    assert "--dry-run" in script_text, "--dry-run flag missing"


def test_setup_sh_uses_visudo_validation(script_text: str) -> None:
    """Atomic sudoers replacement must validate via `visudo -c -f`."""
    assert "visudo -c -f" in script_text, (
        "atomic sudoers replacement using `visudo -c -f` missing"
    )


def test_setup_sh_version_skip_uses_git_sha(script_text: str) -> None:
    """Version-skip restart must consult `git_sha`."""
    assert "git_sha" in script_text, "git_sha version-skip check missing"


def test_setup_sh_no_todo_fixme(script_text: str) -> None:
    """No TODO/FIXME markers may be committed."""
    assert "TODO" not in script_text, "TODO marker present"
    assert "FIXME" not in script_text, "FIXME marker present"


def test_setup_sh_under_500_lines() -> None:
    """File must remain at or under 500 lines (CI constraint)."""
    line_count = len(SETUP_SH.read_text().splitlines())
    assert line_count <= 500, f"setup.sh is {line_count} lines, must be <=500"


def test_setup_sh_uses_strict_mode(script_text: str) -> None:
    """`set -euo pipefail` must remain enabled."""
    assert "set -euo pipefail" in script_text, "strict mode missing"
