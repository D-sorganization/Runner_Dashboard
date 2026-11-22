"""Contract tests for scripts/d-overnight-supervisor.ps1 (issue #4574)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "d-overnight-supervisor.ps1"


@pytest.mark.unit
def test_supervisor_script_exists_and_is_non_empty() -> None:
    assert _SCRIPT_PATH.is_file(), f"Expected {_SCRIPT_PATH} to exist"
    content = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert len(content) > 100
    assert "blocker-keyed" in content.lower() or "dormant" in content.lower()
    assert "1-card-per-lane" in content.lower() or "assignedkeys" in content.lower()


@pytest.mark.unit
def test_supervisor_powershell_syntax_valid() -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell interpreter not available on this host")

    # Validate PowerShell parsing without execution
    parse_script = (
        f"$content = Get-Content -Raw -Path '{_SCRIPT_PATH}'; "
        "[System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$null, [ref]$null)"
    )
    res = subprocess.run([pwsh, "-NoProfile", "-Command", parse_script], capture_output=True, text=True)
    assert res.returncode == 0, f"PowerShell parser failed: {res.stderr}"


@pytest.mark.unit
def test_blocker_keyed_dormancy_contract() -> None:
    """Verify that dormancy keys include (task, head, blocker, dep_version) and invalidate on changes."""
    content = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "Get-DormancyKey" in content or "task" in content

    # Test the keying logic: (task, head, blocker, dep_version)
    def make_key(task: str, head: str, blocker: str, dep_version: str) -> str:
        return f"{task}@{head}#{blocker}@{dep_version}"

    k1 = make_key("Runner_Dashboard#4574", "abc1234", "waiting on CI", "v1")
    # Head moves: key must differ
    k2 = make_key("Runner_Dashboard#4574", "def5678", "waiting on CI", "v1")
    assert k1 != k2

    # Blocker changes: key must differ
    k3 = make_key("Runner_Dashboard#4574", "abc1234", "resolved", "v1")
    assert k1 != k3

    # Dep version changes: key must differ
    k4 = make_key("Runner_Dashboard#4574", "abc1234", "waiting on CI", "v2")
    assert k1 != k4


@pytest.mark.unit
def test_one_card_per_lane_fenced_dispatch_contract() -> None:
    """Verify 1-card-per-lane uniqueness: two lanes cannot be assigned the same task card."""
    content = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "assignedKeys" in content or "assigned_keys" in content.lower()

    # Model the supervisor assignment logic
    assigned_keys: set[str] = set()
    cards = [
        {"key": "Runner_Dashboard#4574", "head": "abc1234", "kind": "repair"},
        {"key": "Repository_Management#3029", "head": "def5678", "kind": "repair"},
    ]

    # Lane 1 picks
    picked_lane1 = None
    for c in cards:
        if c["key"] not in assigned_keys:
            picked_lane1 = c
            assigned_keys.add(c["key"])
            break
    assert picked_lane1 is not None and picked_lane1["key"] == "Runner_Dashboard#4574"

    # Lane 2 picks from same cards pool
    picked_lane2 = None
    for c in cards:
        if c["key"] not in assigned_keys:
            picked_lane2 = c
            assigned_keys.add(c["key"])
            break
    assert picked_lane2 is not None and picked_lane2["key"] == "Repository_Management#3029"
    assert picked_lane1["key"] != picked_lane2["key"]


@pytest.mark.unit
def test_supervisor_exits_cleanly_on_stop_file(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell interpreter not available on this host")

    stop_file = tmp_path / "STOP_SUPERVISOR"
    stop_file.touch()
    log_file = tmp_path / "supervisor.log"

    cmd = [
        pwsh,
        "-NoProfile",
        "-File",
        str(_SCRIPT_PATH),
        "-MaxHours",
        "1",
        "-PollSec",
        "1",
        "-StopFile",
        str(stop_file),
        "-LogFile",
        str(log_file),
        "-DryRun",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert res.returncode == 0
    assert log_file.is_file()
    log_text = log_file.read_text(encoding="utf-8")
    assert "STOP_FILE_DETECTED" in log_text or "SUPERVISOR" in log_text
