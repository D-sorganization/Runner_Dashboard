"""Contract tests for the 500-line cap's exempt-path matching (RD-33, #2948).

The old `.github/workflows/ci-standard.yml` "Verify no source file exceeds
500 lines" step filtered paths with an unanchored substring regex. A file
was wrongly exempt if its path merely *contained* an exempt filename, e.g.
"backend/staff/board_queue.py" matched "queue.py", and
"backend/tailnet_identity.py" matched "identity.py" (RD-33). These tests run
the actual step script (extracted from the workflow, like the sibling tests
in test_workflow_hygiene.py) against synthetic changed files, so they
exercise the real contract rather than a reimplementation of it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from bash_host import require_bash
from test_workflow_hygiene import _ci_step

_LINECAP_STEP = "Verify no source file exceeds 500 lines"


def _exempt_paths() -> list[str]:
    """Parse the step's inline `EXEMPT_PATHS=$(cat <<'PATHS' ... PATHS)` heredoc."""
    run = _ci_step("ci-health-check", name=_LINECAP_STEP)["run"]
    match = re.search(r"<<'PATHS'\n(.*?\n)PATHS\n", run, re.DOTALL)
    assert match, "could not find the EXEMPT_PATHS heredoc in the step"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _linecap_pr_checkout(tmp_path: Path, *, changed_path: str, lines: int) -> tuple[Path, str]:
    """Build a depth-1 PR checkout like actions/checkout does; return (dir, base_sha)."""
    origin = tmp_path / "origin"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    (origin / "backend").mkdir()
    (origin / "backend" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "base")
    base_sha = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "-q", "-b", "pr")
    target = origin / changed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n" * lines, encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "pr")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--depth=1", "--branch", "pr", origin.as_uri(), str(work))
    return work, base_sha


def _run_linecap(work: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full_env = {"PATH": os.environ.get("PATH", ""), "HOME": str(work.parent), **env}
    return subprocess.run(
        [require_bash(), "--noprofile", "--norc", "-e", "-c", _ci_step("ci-health-check", name=_LINECAP_STEP)["run"]],
        cwd=work,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_exempt_list_does_not_exempt_lookalike_paths(tmp_path: Path) -> None:
    lookalikes = [
        "backend/tailnet_identity.py",
        "backend/board_queue.py",
        "backend/totals.py",
    ]
    for lookalike in lookalikes:
        work, base_sha = _linecap_pr_checkout(tmp_path / lookalike.replace("/", "_"), changed_path=lookalike, lines=501)
        result = _run_linecap(work, {"EVENT_NAME": "pull_request", "BASE_SHA": base_sha})
        assert result.returncode == 1, f"{lookalike} must not be exempt:\n{result.stdout}{result.stderr}"
        assert lookalike in result.stdout


def test_exempt_list_still_exempts_listed_files(tmp_path: Path) -> None:
    listed = _exempt_paths()
    assert listed, "EXEMPT_PATHS heredoc must not be empty"
    for exempt_path in listed:
        work, base_sha = _linecap_pr_checkout(
            tmp_path / exempt_path.replace("/", "_"), changed_path=exempt_path, lines=501
        )
        result = _run_linecap(work, {"EVENT_NAME": "pull_request", "BASE_SHA": base_sha})
        assert result.returncode == 0, f"{exempt_path} must stay exempt:\n{result.stdout}{result.stderr}"


def test_exempt_list_has_no_duplicate_entries() -> None:
    listed = _exempt_paths()
    assert len(listed) == len(set(listed)), "EXEMPT_PATHS heredoc has a duplicate entry"
