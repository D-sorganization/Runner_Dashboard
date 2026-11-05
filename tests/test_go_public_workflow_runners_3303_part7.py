"""Go-public prep part 7: priority-expedite-guard on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "priority-expedite-guard.yml"


def test_guard_job_uses_ubuntu_latest() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert data["jobs"]["guard"]["runs-on"] == "ubuntu-latest"


def test_guard_job_repeats_workflow_permissions() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    perms = data["jobs"]["guard"].get("permissions") or {}
    assert perms.get("pull-requests") == "write"
    assert perms.get("issues") == "write"
    assert set(perms) <= {"pull-requests", "issues"}


def test_priority_expedite_guard_allowlisted_in_local_only_guard() -> None:
    text = (ROOT / ".github" / "workflows" / "local-only-runner-guard.yml").read_text(
        encoding="utf-8"
    )
    assert '"priority-expedite-guard.yml"' in text
