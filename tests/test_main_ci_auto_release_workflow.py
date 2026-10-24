"""Contract tests for main-ci-auto-release workflow."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "main-ci-auto-release.yml"
SHA_PINNED_RE = re.compile(r"^[\w.\-]+/[\w.\-]+(?:/[\w.\-/]+)?@[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    if True in data and "on" not in data:
        data["on"] = data[True]
    return data


def test_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_workflow_run_triggers_on_main_ci_completion() -> None:
    cfg = _load(WORKFLOW)
    wr = cfg["on"]["workflow_run"]
    assert wr["workflows"] == ["CI Standard"]
    assert wr["branches"] == ["main"]
    assert wr["types"] == ["completed"]


def test_permissions_allow_check_run_reads_for_gate_wait() -> None:
    perms = _load(WORKFLOW)["permissions"]
    assert perms == {"actions": "read", "contents": "write"}


def test_same_repo_guard_on_publish_job() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run.head_repository.full_name == github.repository" in text
    assert "workflow_run.head_branch == 'main'" in text
    assert "github.event_name == 'workflow_run'" in text


def test_actions_are_sha_pinned() -> None:
    uses = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("uses:"):
            value = stripped.split(":", 1)[1].strip().split("#", 1)[0].strip().strip("'\"")
            uses.append(value)
    assert uses
    for ref in uses:
        assert SHA_PINNED_RE.match(ref), f"unpinned action: {ref}"


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS cannot represent the POSIX executable bit")
def test_publish_script_is_executable() -> None:
    script = ROOT / "deploy" / "publish-main-ci-release.sh"
    assert script.is_file()
    assert (script.stat().st_mode & 0o111) != 0


def test_publish_job_sets_up_python_for_wheelhouse() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/setup-python@" in text
    assert 'python-version: "3.12"' in text
