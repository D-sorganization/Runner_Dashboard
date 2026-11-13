"""Go-public guards for private fleet topology (issues #3304 / #3305)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Pre-go-public MagicDNS suffix; must not appear in tracked deploy docs or runbooks.
_PRIVATE_TAILNET_LABEL = "tail2bbcc7"


def _git_tracked_paths() -> set[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=REPO, text=True)
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_live_machine_registry_is_not_tracked() -> None:
    tracked = _git_tracked_paths()
    assert "backend/machine_registry.yml" not in tracked
    assert "backend/machine_registry.example.yml" in tracked


def test_gitignore_excludes_live_machine_registry() -> None:
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "machine_registry.yml" in ignore


def test_deploy_and_runbook_docs_have_no_private_tailnet_names() -> None:
    paths = [
        REPO / "deploy" / "setup.sh",
        REPO / "deploy" / "scheduled-dashboard-maintenance.sh",
        *sorted((REPO / "docs" / "runbooks").glob("*.md")),
    ]
    offenders: list[str] = []
    for path in paths:
        if _PRIVATE_TAILNET_LABEL in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"private tailnet label still present in: {offenders}"
