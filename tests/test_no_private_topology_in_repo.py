"""Gate private fleet topology out of tracked files (public-repo hygiene).

Complements ``tests/test_no_secrets_in_repo.py`` and the gitleaks workflow.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from private_topology_guard import TopologyFinding, format_findings, scan_text_line

REPO_ROOT = Path(__file__).resolve().parent.parent

_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "backend/private_topology_guard.py",
        "tests/test_no_private_topology_in_repo.py",
    }
)

_ALLOWED_PREFIXES: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    ".git/",
)


def _git_tracked_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / line for line in out.splitlines() if line.strip()]


def _is_allowed(rel_path: str) -> bool:
    if rel_path in _ALLOWED_PATHS:
        return True
    return any(rel_path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


def test_repo_has_tracked_files() -> None:
    files = _git_tracked_files()
    if not files:
        pytest.skip("No git-tracked files (running outside a git checkout).")
    assert any(p.suffix == ".py" for p in files)


def test_no_private_topology_in_tracked_files() -> None:
    files = _git_tracked_files()
    if not files:
        pytest.skip("No git-tracked files (running outside a git checkout).")

    findings: list[tuple[str, int, TopologyFinding]] = []

    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _is_allowed(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            for finding in scan_text_line(line):
                findings.append((rel, lineno, finding))

    assert not findings, (
        "Private fleet topology found in tracked files. Replace with documented "
        "examples (hub-a / node-b, fleet-example.ts.net, 100.64.0.0/24 or "
        "100.64.254.0/24) or move live values to ~/.config/runner-dashboard/. "
        "Findings:\n  " + format_findings(findings)
    )


def test_guard_allows_documented_example_tailnet() -> None:
    assert not scan_text_line("https://hub-a.fleet-example.ts.net:8321/api")


def test_guard_blocks_non_example_ts_net() -> None:
    findings = scan_text_line("https://hub-a.not-example.ts.net:8321")
    assert any(f.rule == "ts_net_host" for f in findings)
