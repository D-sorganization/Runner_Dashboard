"""Tests for ``deploy/ollama-wsl-bridge.ps1`` (issue #1257).

The script changes Windows networking (``netsh interface portproxy``, a
firewall rule, a scheduled task), so only its pure helpers run here: they are
dot-sourced with ``-LibraryOnly`` and driven with fixture state. The helpers
decide everything; the side-effect functions only execute the plan.
Skipped when PowerShell is absent (Linux CI without ``pwsh``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "ollama-wsl-bridge.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(SHELL is None, reason="PowerShell not available")

PORTPROXY_TABLE = """
Listen on ipv4:             Connect to ipv4:

Address         Port        Address         Port
--------------- ----------  --------------- ----------
0.0.0.0         8321        172.21.32.154   8321
192.168.208.1   11434       127.0.0.1       11434
"""


def _ps(body: str) -> Any:
    driver = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        . '{SCRIPT}' -LibraryOnly
        $result = & {{
        {body}
        }}
        $result | ConvertTo-Json -Depth 6 -Compress
        """
    )
    assert SHELL is not None
    proc = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", driver],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _fwd(listen: str, target: str = "127.0.0.1", lport: int = 11434, tport: int = 11434) -> str:
    """One portproxy row as a PowerShell object literal."""
    return (
        f"[pscustomobject]@{{ListenAddress='{listen}';ListenPort={lport};"
        f"ConnectAddress='{target}';ConnectPort={tport}}}"
    )


def _plan(**kw: Any) -> dict[str, Any]:
    args = {
        "AdapterIp": "'172.21.32.1'",
        "PrefixLength": "20",
        "Port": "11434",
        "Forwards": "@()",
        "RecordedListen": "''",
        "OllamaListen": "@('127.0.0.1')",
    }
    args.update(kw)
    call = " ".join(f"-{k} {v}" for k, v in args.items())
    return _ps(f"Get-OllamaBridgePlan {call}")


def test_script_parses_and_declares_modes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for token in ("-LibraryOnly", "'Apply'", "'Install'", "'Uninstall'", "'Status'", "[switch]$DryRun"):
        assert token.replace("-", "") in text.replace("-", "")
    _ps("'ok'")


def test_subnet_from_adapter_address() -> None:
    assert _ps("Get-SubnetCidr -IpAddress '192.168.208.1' -PrefixLength 20") == "192.168.208.0/20"
    assert _ps("Get-SubnetCidr -IpAddress '172.21.32.1' -PrefixLength 20") == "172.21.32.0/20"
    assert _ps("Get-SubnetCidr -IpAddress '10.1.2.3' -PrefixLength 24") == "10.1.2.0/24"


def test_portproxy_table_parses() -> None:
    table = PORTPROXY_TABLE.replace("'", "''")
    rows = _ps(f"@(ConvertFrom-PortProxyTable -Text '{table}')")
    assert rows == [
        {"ListenAddress": "0.0.0.0", "ListenPort": 8321, "ConnectAddress": "172.21.32.154", "ConnectPort": 8321},
        {"ListenAddress": "192.168.208.1", "ListenPort": 11434, "ConnectAddress": "127.0.0.1", "ConnectPort": 11434},
    ]


def test_fresh_node_adds_forward_and_rule() -> None:
    plan = _plan()
    assert plan["Status"] == "apply"
    assert plan["Subnet"] == "172.21.32.0/20"
    assert [a["Op"] for a in plan["Actions"]] == ["add-forward", "set-rule"]
    assert plan["Actions"][0]["ListenAddress"] == "172.21.32.1"


def test_already_configured_only_refreshes_rule() -> None:
    fwd = f"@({_fwd('172.21.32.1')})"
    plan = _plan(Forwards=fwd, RecordedListen="'172.21.32.1'")
    assert plan["Status"] == "apply"
    assert [a["Op"] for a in plan["Actions"]] == ["set-rule"]


def test_adapter_address_changed_removes_only_our_stale_forward() -> None:
    fwd = f"@({_fwd('192.168.208.1')},{_fwd('0.0.0.0', '172.21.32.154', 8321, 8321)})"
    plan = _plan(AdapterIp="'192.168.224.1'", Forwards=fwd, RecordedListen="'192.168.208.1'")
    ops = [(a["Op"], a.get("ListenAddress"), a.get("ListenPort")) for a in plan["Actions"]]
    assert ops == [
        ("remove-forward", "192.168.208.1", 11434),
        ("add-forward", "192.168.224.1", 11434),
        ("set-rule", None, None),
    ]


def test_foreign_forward_on_our_address_is_refused() -> None:
    fwd = f"@({_fwd('172.21.32.1', '10.9.9.9', 11434, 80)})"
    plan = _plan(Forwards=fwd)
    assert plan["Status"] == "refused"
    assert plan["Actions"] == []
    assert "10.9.9.9" in plan["Reason"]


def test_unrecorded_stale_forward_is_left_alone() -> None:
    fwd = f"@({_fwd('192.168.208.1')})"
    plan = _plan(Forwards=fwd, RecordedListen="''")
    assert [a["Op"] for a in plan["Actions"]] == ["add-forward", "set-rule"]


def test_exposed_ollama_is_refused_until_owner_turns_off_network_exposure() -> None:
    for listen in ("@('::')", "@('0.0.0.0')", "@('127.0.0.1','172.21.32.1')"):
        plan = _plan(OllamaListen=listen)
        assert plan["Status"] == "ollama-exposed", listen
        assert plan["Actions"] == []


def test_ollama_not_running_still_installs_bridge() -> None:
    plan = _plan(OllamaListen="@()")
    assert plan["Status"] == "apply"
    assert plan["OllamaRunning"] is False


def test_no_wsl_adapter_does_nothing() -> None:
    plan = _plan(AdapterIp="''", PrefixLength="0")
    assert plan["Status"] == "no-wsl-adapter"
    assert plan["Actions"] == []
