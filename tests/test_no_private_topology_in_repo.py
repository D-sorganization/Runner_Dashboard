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
        # Unit tests for the scanner itself exercise the blocked tokens as
        # fixture input on purpose.
        "tests/test_private_topology_guard.py",
        # The sanitizer's own rename-mapping table necessarily names both the
        # real tokens and their example replacements (#3461 epic child #1).
        "scripts/sanitize_private_topology.py",
        # #3080's self-test canary deliberately builds "...@gmail.com" at
        # runtime to prove the publish guard's own secret scan works; no
        # real address is shipped.
        "scripts/publish_guard.py",
    }
)

_ALLOWED_PREFIXES: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    ".git/",
)

# TODO(#2657): Drop deploy/release exclusions once the held deploy sanitize PR merges.
_HELD_UNTIL_DEPLOY_SANITIZE: tuple[str, ...] = (
    "deploy/",
    ".github/workflows/deploy-qualified-release.yml",
    "docs/runbooks/qualified-release-deploy.md",
    # Asserts against the real deploy script's/workflow's literal content;
    # same hold as the deploy/ tree itself, not a standalone leak.
    "tests/deploy/test_fleet_health_monitor.py",
    "tests/test_qualified_release_deploy_workflow.py",
)

# TODO(#3475): Drop once config/storage-gc-workstations.json and
# config/runner-schedule-oglaptop.json gain an override-path like
# machine_registry.yml's load_machine_registry(path), so live values can
# move out of the tracked file without an entangled test rewrite.
_HELD_FOR_CONFIG_OVERRIDE_PATH: tuple[str, ...] = (
    "config/storage-gc-workstations.json",
    "tests/deploy/test_fleet_storage_gc.py",
    "tests/deploy/test_runner_scheduler.py",
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
    if any(rel_path.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        return True
    if rel_path in _HELD_UNTIL_DEPLOY_SANITIZE:
        return True
    if rel_path in _HELD_FOR_CONFIG_OVERRIDE_PATH:
        return True
    return rel_path.startswith("deploy/")


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
