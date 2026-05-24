"""WSL keepalive diagnostics — wslconfig, systemd, and Windows Scheduled Task.

This module is a pure extraction of the keepalive inspection functions that
previously lived in server.py.  It depends only on:
  - platform_utils.wsl_paths  (same extraction batch)
  - run_cmd                    (imported from server at runtime to avoid cycles)

Constants (WSL_KEEPALIVE_SERVICE, WSL_KEEPALIVE_TASK_NAME) are read from
environment variables at import time, matching the original server.py behaviour.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Literal

from platform_utils.wsl_paths import (
    _candidate_wslconfig_paths,
    _resolve_powershell_executable,
)
from pydantic import BaseModel
from system_utils import run_cmd  # noqa: E402

log = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Module-level constants (mirrors server.py)
# ---------------------------------------------------------------------------

WSL_KEEPALIVE_SERVICE: str = os.environ.get("WSL_KEEPALIVE_SERVICE", "wsl-runner-keepalive.service")
WSL_KEEPALIVE_TASK_NAME: str = os.environ.get("WSL_KEEPALIVE_TASK_NAME", "WSL-Runner-KeepAlive")


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class KeepaliveReport(BaseModel):
    """Structured result for a single keepalive health check."""

    status: Literal[
        "ok",
        "missing",
        "invalid",
        "error",
        "healthy",
        "misconfigured",
        "unsupported",
        "unknown",
        "legacy",
    ]
    detail: str
    source_path: Path | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_vm_idle_timeout(text: str) -> str | None:
    """Extract the vmIdleTimeout value from .wslconfig content.

    Pre-condition: text is a str.
    Post-condition: returns a stripped string or None.
    """
    assert isinstance(text, str), f"text must be str, got {type(text)!r}"

    match = re.search(
        r"(?im)^\s*vmIdleTimeout\s*=\s*([^#;\r\n]+)",
        text,
    )
    if not match:
        return None
    result = match.group(1).strip()
    assert isinstance(result, str)
    return result


def _parse_task_action(action: dict) -> dict:
    """Normalise a scheduled task action for downstream inspection.

    Pre-condition: action is a dict.
    Post-condition: returned dict has 'execute' and 'arguments' keys.
    """
    assert isinstance(action, dict), f"action must be dict, got {type(action)!r}"

    result = {
        "execute": action.get("Execute") or action.get("execute"),
        "arguments": action.get("Arguments") or action.get("arguments"),
    }
    assert "execute" in result and "arguments" in result
    return result


def _probe_detail(probe: dict, fallback: str) -> str:
    """Return human-readable probe detail even for partially failed probes.

    Pre-condition: probe is a dict, fallback is a str.
    """
    assert isinstance(probe, dict), f"probe must be dict, got {type(probe)!r}"
    assert isinstance(fallback, str), f"fallback must be str, got {type(fallback)!r}"

    return str(probe.get("detail") or probe.get("error") or fallback)


def _detect_legacy_keepalive(
    actions: list[dict],
    startup_vbs_files: list[str],
) -> tuple[bool, str | None]:
    """Detect the old VBS/fire-and-forget keepalive pattern.

    Pre-condition: actions is a list of dicts; startup_vbs_files is a list of str.
    Post-condition: returns (found: bool, detail: str | None).
    """
    assert isinstance(actions, list), f"actions must be list, got {type(actions)!r}"
    assert isinstance(startup_vbs_files, list), f"startup_vbs_files must be list, got {type(startup_vbs_files)!r}"

    if startup_vbs_files:
        return True, f"Legacy VBS file(s) still present: {', '.join(startup_vbs_files)}"

    for action in actions:
        execute = (action.get("execute") or "").lower()
        arguments = (action.get("arguments") or "").lower()
        if execute.endswith("wscript.exe") or execute.endswith("cscript.exe"):
            return True, f"Task launches {execute.rsplit('/', 1)[-1]} directly."
        if ".vbs" in execute or ".vbs" in arguments:
            return True, "Task still references a .vbs keepalive script."

    return False, None


# ---------------------------------------------------------------------------
# .wslconfig inspection (sync)
# ---------------------------------------------------------------------------


def _inspect_wslconfig() -> dict:
    """Inspect .wslconfig for the vmIdleTimeout keepalive setting.

    Post-condition: returns a dict with at minimum 'status' and 'configured' keys.
    """
    checked_paths = [str(path) for path in _candidate_wslconfig_paths()]
    for path in _candidate_wslconfig_paths():
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            result = {
                "status": "unknown",
                "path": str(path),
                "checked_paths": checked_paths,
                "error": str(exc),
                "configured": False,
            }
            assert "status" in result and "configured" in result
            return result

        vm_idle_timeout = _parse_vm_idle_timeout(content)
        if vm_idle_timeout is None:
            result = {
                "status": "misconfigured",
                "path": str(path),
                "checked_paths": checked_paths,
                "configured": True,
                "vm_idle_timeout": None,
                "idle_shutdown_disabled": False,
                "detail": "vmIdleTimeout was not found in .wslconfig.",
            }
            assert "status" in result and "configured" in result
            return result

        disabled = vm_idle_timeout == "-1"
        result = {
            "status": "healthy" if disabled else "misconfigured",
            "path": str(path),
            "checked_paths": checked_paths,
            "configured": True,
            "vm_idle_timeout": vm_idle_timeout,
            "idle_shutdown_disabled": disabled,
            "detail": (
                "vmIdleTimeout=-1 disables WSL idle shutdown."
                if disabled
                else f"vmIdleTimeout is {vm_idle_timeout}, not -1."
            ),
        }
        assert "status" in result and "configured" in result
        return result

    result = {
        "status": "missing",
        "path": None,
        "checked_paths": checked_paths,
        "configured": False,
        "vm_idle_timeout": None,
        "idle_shutdown_disabled": False,
        "detail": "No .wslconfig file was found in the configured locations.",
    }
    assert "status" in result and "configured" in result
    return result


# ---------------------------------------------------------------------------
# systemd keepalive inspection (async)
# ---------------------------------------------------------------------------


async def _inspect_systemd_keepalive() -> dict:
    """Inspect the in-WSL systemd keepalive service.

    Post-condition: returns a dict with 'status', 'service', 'configured',
                    'active', and 'enabled' keys.
    """
    if os.name == "nt":
        result = {
            "status": "unsupported",
            "service": WSL_KEEPALIVE_SERVICE,
            "configured": False,
            "active": False,
            "enabled": False,
            "detail": (
                "systemd keepalive is checked inside WSL; "
                "this Windows fallback process cannot query systemctl directly."
            ),
        }
        assert "status" in result
        return result

    code, stdout, stderr = await run_cmd(
        [
            "systemctl",
            "show",
            WSL_KEEPALIVE_SERVICE,
            "--property=LoadState,ActiveState,UnitFileState,FragmentPath,Description",
            "--no-pager",
        ],
        timeout=10,
    )

    if code != 0:
        lower = f"{stdout}\n{stderr}".lower()
        if "system has not been booted with systemd" in lower or "failed to connect to bus" in lower:
            result = {
                "status": "unsupported",
                "service": WSL_KEEPALIVE_SERVICE,
                "configured": False,
                "active": False,
                "enabled": False,
                "detail": "systemd is not available in this WSL session.",
            }
            assert "status" in result
            return result
        result = {
            "status": "unknown",
            "service": WSL_KEEPALIVE_SERVICE,
            "configured": False,
            "active": False,
            "enabled": False,
            "error": stderr.strip() or stdout.strip() or "systemctl show failed",
        }
        assert "status" in result
        return result

    props: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()

    load_state = props.get("LoadState")
    active_state = props.get("ActiveState")
    unit_file_state = props.get("UnitFileState")
    configured = load_state == "loaded"
    active = active_state == "active"
    enabled = unit_file_state == "enabled"
    healthy = configured and active and enabled
    if healthy:
        detail = f"{WSL_KEEPALIVE_SERVICE} is active and enabled."
        status = "healthy"
    elif configured:
        detail = f"{WSL_KEEPALIVE_SERVICE} is {active_state or 'unknown'} and {unit_file_state or 'unknown'}."
        status = "misconfigured"
    else:
        detail = f"{WSL_KEEPALIVE_SERVICE} is not installed."
        status = "missing"

    result = {
        "status": status,
        "service": WSL_KEEPALIVE_SERVICE,
        "configured": configured,
        "active": active,
        "enabled": enabled,
        "load_state": load_state,
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "fragment_path": props.get("FragmentPath"),
        "description": props.get("Description"),
        "detail": detail,
    }
    assert "status" in result
    return result


# ---------------------------------------------------------------------------
# Windows Scheduled Task keepalive inspection (async)
# ---------------------------------------------------------------------------


async def _inspect_windows_keepalive() -> dict:
    """Inspect the Windows Scheduled Task and legacy keepalive artifacts.

    Post-condition: returns a dict with 'status' and 'task_found' keys.
    """
    powershell = _resolve_powershell_executable()
    if powershell is None:
        result = {
            "status": "unsupported",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": ("PowerShell was not found; Windows Scheduled Task cannot be queried from this WSL session."),
        }
        assert "status" in result
        return result

    _ps_get_legacy = (
        "@(Get-ChildItem -Path $startup -Filter 'wsl-keepalive.vbs'"
        " -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)"
    )
    _ps_get_actions = (
        "@($task.Actions | ForEach-Object { [pscustomobject]@{ Execute = $_.Execute; Arguments = $_.Arguments } })"  # noqa: E501
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$legacy = {_ps_get_legacy}
$task = $null
try {{
    $task = Get-ScheduledTask -TaskName '{WSL_KEEPALIVE_TASK_NAME}' -ErrorAction Stop
    $actions = {_ps_get_actions}
    $result = [pscustomobject]@{{
        task_found = $true
        task_name = $task.TaskName
        state = "$($task.State)"
        actions = $actions
        startup_vbs_files = $legacy
    }}
}} catch {{
    $result = [pscustomobject]@{{
        task_found = $false
        task_name = '{WSL_KEEPALIVE_TASK_NAME}'
        state = $null
        actions = @()
        startup_vbs_files = $legacy
        error = $_.Exception.Message
    }}
}}
$result | ConvertTo-Json -Depth 5
"""
    try:
        code, stdout, stderr = await run_cmd(
            [powershell, "-NoProfile", "-Command", script],
            timeout=12,
        )
    except OSError as exc:
        result = {
            "status": "unsupported",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": f"PowerShell execution failed: {exc}",
        }
        assert "status" in result
        return result

    if code != 0:
        result = {
            "status": "unknown",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": stderr.strip() or stdout.strip() or "Scheduled task query failed.",
        }
        assert "status" in result
        return result

    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {}

    raw_actions = payload.get("actions") or []
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]
    actions = [_parse_task_action(action) for action in raw_actions]
    startup_vbs_files = payload.get("startup_vbs_files") or []
    if isinstance(startup_vbs_files, str):
        startup_vbs_files = [startup_vbs_files]

    legacy_vbs_detected, legacy_detail = _detect_legacy_keepalive(
        actions,
        [str(item) for item in startup_vbs_files],
    )

    state = payload.get("state")
    task_found = bool(payload.get("task_found"))
    running = state == "Running"
    action_exec = actions[0]["execute"] if actions else None
    action_args = actions[0]["arguments"] if actions else None
    if task_found and running and not legacy_vbs_detected:
        status = "healthy"
        detail = f"{WSL_KEEPALIVE_TASK_NAME} is Running."
    elif legacy_vbs_detected:
        status = "legacy"
        detail = legacy_detail or "Legacy VBS keepalive detected."
    elif task_found:
        status = "misconfigured" if state == "Ready" else "unknown"
        detail = f"{WSL_KEEPALIVE_TASK_NAME} is {state or 'unknown'}." + (
            f" Action: {action_exec or 'n/a'} {action_args or ''}".rstrip()
        )
    else:
        status = "missing"
        detail = payload.get("error") or f"{WSL_KEEPALIVE_TASK_NAME} is not registered."

    result = {
        "status": status,
        "task_name": WSL_KEEPALIVE_TASK_NAME,
        "task_found": task_found,
        "state": state,
        "actions": actions,
        "startup_vbs_files": [str(item) for item in startup_vbs_files],
        "legacy_vbs_detected": legacy_vbs_detected,
        "legacy_vbs_detail": legacy_detail,
        "detail": detail,
    }
    assert "status" in result
    return result
