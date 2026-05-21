"""WSL keepalive inspection helpers.

Extracted from server.py (issue #2942) — all functions related to checking
whether the WSL idle-shutdown prevention stack is correctly configured:
  - .wslconfig (vmIdleTimeout)
  - systemd keepalive service
  - Windows Scheduled Task

Public API
----------
_inspect_wslconfig()
_inspect_systemd_keepalive()
_inspect_windows_keepalive()
_watchdog_status_impl()  — aggregated result, cached

Helper utilities
----------------
_windows_path_to_wsl()
_dedupe_paths()
_candidate_wslconfig_paths()
_parse_vm_idle_timeout()
_parse_task_action()
_probe_detail()
_detect_legacy_keepalive()
_resolve_powershell_executable()
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cache_utils import cache_get as _cache_get, cache_set as _cache_set

log = logging.getLogger("dashboard")


# ---------------------------------------------------------------------------
# Environment-dependent names (overridable via env vars so tests can mock)
# ---------------------------------------------------------------------------

WSL_KEEPALIVE_SERVICE = os.environ.get("WSL_KEEPALIVE_SERVICE", "wsl-runner-keepalive.service")
WSL_KEEPALIVE_TASK_NAME = os.environ.get("WSL_KEEPALIVE_TASK_NAME", "WSL-Runner-KeepAlive")


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def _windows_path_to_wsl(raw_path: str) -> Path:
    """Convert a Windows path to its WSL mount equivalent when possible.

    Precondition: raw_path is a non-empty string.
    Postcondition: returns a Path; never raises.
    """
    assert isinstance(raw_path, str), "raw_path must be a str"
    normalized = raw_path.strip().strip('"')
    match = re.match(r"^([a-zA-Z]):[\\/](.*)$", normalized)
    if not match:
        return Path(normalized)
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/")
    return Path("/mnt") / drive / tail


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Return paths in insertion order without duplicates.

    Postcondition: len(result) <= len(paths), order preserved.
    """
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    assert len(deduped) <= len(paths), "deduplication must not add entries"
    return deduped


def _candidate_wslconfig_paths() -> list[Path]:
    """Return plausible .wslconfig locations for the current user."""
    candidates: list[Path] = []
    for env_name in ("WSL_KEEPALIVE_WSLCONFIG_PATH", "WSL_CONFIG_PATH"):
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    profile = os.environ.get("USERPROFILE")
    if profile:
        profile_path = Path(profile).expanduser()
        if os.name == "nt":
            candidates.append(profile_path / ".wslconfig")
        candidates.append(_windows_path_to_wsl(profile) / ".wslconfig")

    home_drive = os.environ.get("HOMEDRIVE")
    home_path = os.environ.get("HOMEPATH")
    if home_drive and home_path:
        windows_home = f"{home_drive}{home_path}"
        if os.name == "nt":
            candidates.append(Path(windows_home).expanduser() / ".wslconfig")
        candidates.append(_windows_path_to_wsl(windows_home) / ".wslconfig")

    users_root = Path("/mnt/c/Users")
    try:
        for profile_dir in users_root.iterdir():
            if not profile_dir.is_dir():
                continue
            if profile_dir.name.lower() in {"all users", "default", "default user", "public"}:
                continue
            candidates.append(profile_dir / ".wslconfig")
    except OSError:
        pass

    return _dedupe_paths(candidates)


# ---------------------------------------------------------------------------
# .wslconfig inspection
# ---------------------------------------------------------------------------


def _parse_vm_idle_timeout(text: str) -> str | None:
    """Extract the vmIdleTimeout value from a .wslconfig file content string.

    Returns None when the setting is absent; the raw value string otherwise.
    """
    match = re.search(r"(?im)^\s*vmIdleTimeout\s*=\s*([^#;\r\n]+)", text)
    if not match:
        return None
    return match.group(1).strip()


def _inspect_wslconfig() -> dict:
    """Inspect .wslconfig for the vmIdleTimeout keepalive setting."""
    checked_paths = [str(path) for path in _candidate_wslconfig_paths()]
    for path in _candidate_wslconfig_paths():
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return {
                "status": "unknown",
                "path": str(path),
                "checked_paths": checked_paths,
                "error": str(exc),
                "configured": False,
            }

        vm_idle_timeout = _parse_vm_idle_timeout(content)
        if vm_idle_timeout is None:
            return {
                "status": "misconfigured",
                "path": str(path),
                "checked_paths": checked_paths,
                "configured": True,
                "vm_idle_timeout": None,
                "idle_shutdown_disabled": False,
                "detail": "vmIdleTimeout was not found in .wslconfig.",
            }

        disabled = vm_idle_timeout == "-1"
        return {
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

    return {
        "status": "missing",
        "path": None,
        "checked_paths": checked_paths,
        "configured": False,
        "vm_idle_timeout": None,
        "idle_shutdown_disabled": False,
        "detail": "No .wslconfig file was found in the configured locations.",
    }


# ---------------------------------------------------------------------------
# Windows Scheduled Task inspection
# ---------------------------------------------------------------------------


def _parse_task_action(action: dict) -> dict:
    """Normalize a scheduled task action for downstream inspection."""
    return {
        "execute": action.get("Execute") or action.get("execute"),
        "arguments": action.get("Arguments") or action.get("arguments"),
    }


def _probe_detail(probe: dict, fallback: str) -> str:
    """Return human-readable probe detail even for partially failed probes."""
    return str(probe.get("detail") or probe.get("error") or fallback)


def _detect_legacy_keepalive(
    actions: list[dict],
    startup_vbs_files: list[str],
) -> tuple[bool, str | None]:
    """Detect the old VBS/fire-and-forget keepalive pattern."""
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


def _resolve_powershell_executable() -> str | None:
    """Find a PowerShell executable from WSL service environments."""
    candidates = [
        os.environ.get("POWERSHELL"),
        "powershell.exe",
        "pwsh.exe",
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_absolute() and path.exists():
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


async def _inspect_windows_keepalive() -> dict:
    """Inspect the Windows Scheduled Task and legacy keepalive artifacts."""
    powershell = _resolve_powershell_executable()
    if powershell is None:
        return {
            "status": "unsupported",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": (
                "PowerShell was not found; "
                "Windows Scheduled Task cannot be queried from this WSL session."
            ),
        }

    _ps_get_legacy = (
        "@(Get-ChildItem -Path $startup -Filter 'wsl-keepalive.vbs'"
        " -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)"
    )
    _ps_get_actions = (
        "@($task.Actions | ForEach-Object"
        " { [pscustomobject]@{ Execute = $_.Execute; Arguments = $_.Arguments } })"
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
        import json

        proc = await asyncio.create_subprocess_exec(
            powershell,
            "-NoProfile",
            "-Command",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=12)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            return {
                "status": "unknown",
                "task_name": WSL_KEEPALIVE_TASK_NAME,
                "task_found": False,
                "state": None,
                "actions": [],
                "startup_vbs_files": [],
                "legacy_vbs_detected": False,
                "detail": "PowerShell query timed out.",
            }
        code = proc.returncode if proc.returncode is not None else -1
        stdout = stdout_b.decode()
        stderr = stderr_b.decode()
    except OSError as exc:
        return {
            "status": "unsupported",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": f"PowerShell execution failed: {exc}",
        }

    if code != 0:
        return {
            "status": "unknown",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": stderr.strip() or stdout.strip() or "Scheduled task query failed.",
        }

    try:
        payload: Any = json.loads(stdout)
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
    ready = state == "Ready"
    action_exec = actions[0]["execute"] if actions else None
    action_args = actions[0]["arguments"] if actions else None
    if task_found and running and not legacy_vbs_detected:
        status = "healthy"
        detail = f"{WSL_KEEPALIVE_TASK_NAME} is Running."
    elif legacy_vbs_detected:
        status = "legacy"
        detail = legacy_detail or "Legacy VBS keepalive detected."
    elif task_found:
        status = "misconfigured" if ready else "unknown"
        detail = f"{WSL_KEEPALIVE_TASK_NAME} is {state or 'unknown'}." + (
            f" Action: {action_exec or 'n/a'} {action_args or ''}".rstrip()
        )
    else:
        status = "missing"
        detail = payload.get("error") or f"{WSL_KEEPALIVE_TASK_NAME} is not registered."

    return {
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


# ---------------------------------------------------------------------------
# systemd keepalive inspection
# ---------------------------------------------------------------------------


async def _run_cmd(
    cmd: list[str],
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Minimal async subprocess helper (LOD — does not reach into server internals)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode(),
            stderr_b.decode(),
        )
    except (TimeoutError, asyncio.TimeoutError):
        proc.kill()
        return -1, "", "Command timed out"


async def _inspect_systemd_keepalive() -> dict:
    """Inspect the in-WSL systemd keepalive service."""
    if os.name == "nt":
        return {
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

    code, stdout, stderr = await _run_cmd(
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
            return {
                "status": "unsupported",
                "service": WSL_KEEPALIVE_SERVICE,
                "configured": False,
                "active": False,
                "enabled": False,
                "detail": "systemd is not available in this WSL session.",
            }
        return {
            "status": "unknown",
            "service": WSL_KEEPALIVE_SERVICE,
            "configured": False,
            "active": False,
            "enabled": False,
            "error": stderr.strip() or stdout.strip() or "systemctl show failed",
        }

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

    return {
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


# ---------------------------------------------------------------------------
# Aggregated watchdog status
# ---------------------------------------------------------------------------


async def watchdog_status_impl(hostname: str) -> dict:
    """Aggregate the WSL keepalive / startup validation state.

    Precondition: hostname is a non-empty string.
    Postcondition: returns a dict with keys: status, summary, hostname,
        checks, wslconfig, systemd_keepalive, windows_task, issues.
    """
    assert hostname, "hostname must be non-empty"

    import datetime as _dt_mod  # noqa: PLC0415

    _UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)

    cached = _cache_get("watchdog", 120.0)  # CacheTtl.WATCHDOG_S
    if cached is not None:
        return cached  # type: ignore[return-value]

    wslconfig, systemd, windows = await asyncio.gather(
        asyncio.to_thread(_inspect_wslconfig),
        _inspect_systemd_keepalive(),
        _inspect_windows_keepalive(),
    )

    checks = [
        {
            "machine": hostname,
            "layer": ".wslconfig",
            "status": wslconfig["status"],
            "detail": _probe_detail(wslconfig, ".wslconfig status unavailable."),
        },
        {
            "machine": hostname,
            "layer": "systemd keepalive",
            "status": systemd["status"],
            "detail": _probe_detail(systemd, "systemd keepalive status unavailable."),
        },
        {
            "machine": hostname,
            "layer": "Windows scheduled task",
            "status": windows["status"],
            "detail": _probe_detail(windows, "Windows scheduled task status unavailable."),
        },
    ]
    issue_details = [c for c in checks if c["status"] not in {"healthy", "unsupported"}]
    issues: list[str] = [
        f"{c['machine']} {c['layer']} ({c['status']}): {c['detail']}" for c in issue_details
    ]

    for check in (wslconfig, systemd, windows):
        if check["status"] not in {"healthy", "unsupported"}:
            check["machine"] = hostname

    if wslconfig["status"] == "healthy" and systemd["status"] == "healthy" and windows["status"] == "healthy":
        overall = "healthy"
        summary = f"{hostname}: all WSL keepalive layers are in place."
    elif all(c["status"] in {"missing", "unknown", "unsupported"} for c in (wslconfig, systemd, windows)):
        overall = "unknown"
        summary = f"{hostname}: WSL keepalive status could not be fully verified."
    elif not issue_details:
        overall = "healthy"
        summary = f"{hostname}: WSL keepalive checks are healthy or unsupported."
    else:
        overall = "degraded"
        summary = f"{hostname}: {len(issue_details)} WSL keepalive check(s) need attention."

    result: dict = {
        "status": overall,
        "summary": summary,
        "hostname": hostname,
        "machine": hostname,
        "timestamp": _dt_mod.datetime.now(_UTC).isoformat(),
        "checks": checks,
        "wslconfig": wslconfig,
        "systemd_keepalive": systemd,
        "windows_task": windows,
        "legacy_vbs_detected": windows.get("legacy_vbs_detected", False),
        "issues": issues,
        "issue_details": issue_details,
        "affected_machines": [hostname] if issues else [],
        "detail": "; ".join(issue for issue in issues if issue),
    }
    assert "status" in result and "summary" in result, "watchdog result must have status+summary"
    _cache_set("watchdog", result)
    return result
