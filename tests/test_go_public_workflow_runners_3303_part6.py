"""Go-public prep part 6: release.yml on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_job_uses_ubuntu_latest() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert data["jobs"]["release"]["runs-on"] == "ubuntu-latest"


def test_release_job_repeats_signing_permissions() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    perms = data["jobs"]["release"].get("permissions") or {}
    assert perms.get("contents") == "write"
    assert perms.get("id-token") == "write"
    assert perms.get("attestations") == "write"
    assert set(perms) <= {"contents", "id-token", "attestations"}


def test_release_workflow_allowlisted_in_local_only_guard() -> None:
    text = (ROOT / ".github" / "workflows" / "local-only-runner-guard.yml").read_text(
        encoding="utf-8"
    )
    assert '"release.yml"' in text
