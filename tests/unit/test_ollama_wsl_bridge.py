"""Exercise the real PowerShell planner without changing host networking."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/windows/ollama-wsl-bridge.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(SHELL is None or sys.platform != "win32", reason="Windows PowerShell is required")
OWNER = "RunnerDashboard.OllamaWslBridge.v1"


@pytest.fixture
def snapshot():
    return {
        "adapters": [{"address": "172.20.16.1", "prefix": 20, "alias": "vEthernet (WSL)"}],
        "forwards": [],
        "rule": None,
        "state": None,
        "task": None,
        "broadRules": [],
        "listeners": ["127.0.0.1"],
        "hostValues": [],
    }


def plan(tmp_path, snapshot, *args):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(snapshot), encoding="utf-8")
    result = subprocess.run(
        [SHELL, "-NoProfile", "-File", str(SCRIPT), "-DryRun", "-SnapshotPath", str(inventory), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}


def test_new_bridge_subnet_and_foreign_port_preserved(tmp_path, snapshot):
    snapshot["forwards"] = [{"address": "0.0.0.0", "port": 8321, "target": "R", "targetPort": 8321}]
    result, value = plan(tmp_path, snapshot, "-Install")
    assert result.returncode == 0, result.stderr
    assert value["subnet"] == "172.20.16.0/20"
    assert value["removeForwards"] == []
    assert value["registerTask"] is True


def owned(snapshot, address="172.20.16.1"):
    snapshot["state"] = {"owner": OWNER, "address": address}
    snapshot["rule"] = {
        "owner": OWNER,
        "address": address,
        "subnet": "172.20.16.0/20",
        "alias": "vEthernet (WSL)",
        "tcp": True,
        "port": 11434,
        "inbound": True,
        "allow": True,
        "enabled": True,
    }
    snapshot["forwards"] = [{"address": address, "port": 11434, "target": "127.0.0.1", "targetPort": 11434}]


def test_existing_owned_bridge_is_idempotent(tmp_path, snapshot):
    owned(snapshot)
    result, value = plan(tmp_path, snapshot)
    assert result.returncode == 0, result.stderr
    assert not value["addForward"]
    assert value["removeForwards"] == []
    assert value["unchanged"]


def test_changed_ip_removes_only_recorded_forward(tmp_path, snapshot):
    owned(snapshot, "172.18.0.1")
    result, value = plan(tmp_path, snapshot)
    assert result.returncode == 0, result.stderr
    assert value["removeForwards"] == ["172.18.0.1"]
    assert value["addForward"]


@pytest.mark.parametrize("collision", ["rule", "forward", "task", "changed-owned-target"])
def test_foreign_resource_is_refused(tmp_path, snapshot, collision):
    if collision == "rule":
        owned(snapshot)
        snapshot["rule"]["owner"] = "foreign"
    elif collision == "task":
        snapshot["task"] = {"owner": "foreign"}
    else:
        owned(snapshot)
        snapshot["forwards"][0]["target"] = "192.0.2.1"
        if collision == "forward":
            snapshot["state"] = None
            snapshot["rule"] = None
    result, value = plan(tmp_path, snapshot, "-Install")
    assert result.returncode != 0
    assert value["status"] == "refused"


def test_uninstall_without_wsl_disables_owned_resources(tmp_path, snapshot):
    owned(snapshot)
    snapshot["adapters"] = []
    result, value = plan(tmp_path, snapshot, "-Uninstall")
    assert result.returncode == 0, result.stderr
    assert value["removeForwards"] == ["172.20.16.1"]
    assert value["disableRule"]
    assert not value["addForward"]


def test_adoption_requires_explicit_flag_and_exact_legacy_shape(tmp_path, snapshot):
    owned(snapshot)
    snapshot["state"] = None
    snapshot["rule"]["owner"] = ""
    result, _ = plan(tmp_path, snapshot)
    assert result.returncode != 0
    result, _ = plan(tmp_path, snapshot, "-AdoptExisting")
    assert result.returncode == 0, result.stderr
    snapshot["rule"]["subnet"] = "Any"
    result, _ = plan(tmp_path, snapshot, "-AdoptExisting")
    assert result.returncode != 0


@pytest.mark.parametrize("field", ["listeners", "hostValues"])
def test_wildcard_ollama_is_refused(tmp_path, snapshot, field):
    snapshot[field] = ["0.0.0.0" if field == "listeners" else "0.0.0.0:11434"]
    result, value = plan(tmp_path, snapshot)
    assert result.returncode != 0
    assert value["status"] == "refused"


def test_unready_wsl_does_not_reconfigure(tmp_path, snapshot):
    snapshot["adapters"] = []
    result, value = plan(tmp_path, snapshot)
    assert result.returncode == 0, result.stderr
    assert value["status"] == "waiting-for-wsl"


def test_interrupted_update_retains_ownership_of_old_forward(tmp_path, snapshot):
    owned(snapshot)
    snapshot["state"]["previousAddresses"] = ["172.18.0.1"]
    snapshot["forwards"].append({"address": "172.18.0.1", "port": 11434, "target": "127.0.0.1", "targetPort": 11434})
    result, value = plan(tmp_path, snapshot)
    assert result.returncode == 0, result.stderr
    assert value["removeForwards"] == ["172.18.0.1"]


def test_broad_ollama_rules_are_disabled_and_require_change(tmp_path, snapshot):
    owned(snapshot)
    snapshot["broadRules"] = ["ollama-broad-123"]
    result, value = plan(tmp_path, snapshot)
    assert result.returncode == 0, result.stderr
    assert value["disableBroadRules"] == ["ollama-broad-123"]
    assert not value["unchanged"]


def test_windows_dotted_mask_legacy_adoption(tmp_path, snapshot):
    owned(snapshot)
    snapshot["state"] = None
    snapshot["rule"]["owner"] = ""
    snapshot["rule"]["subnet"] = "172.20.16.0/255.255.240.0"
    result, value = plan(tmp_path, snapshot, "-AdoptExisting", "-Install")
    assert result.returncode == 0, result.stderr
    assert not value["addForward"]
