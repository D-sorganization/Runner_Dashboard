"""WSL disk status and space reclamation probe (issue #1332).

Provides a read-only view of WSL2 distribution disks (ext4.vhdx):
  - Sparse file attribute on the Windows host
  - VHDX file size on Windows vs space used inside the distribution filesystem
  - Slack space reclaimable via fstrim
  - Status of systemd fstrim.timer and fstrim.service

All host operations are strictly read-only queries and modify nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("dashboard.wsl_disk")

# Read-only PowerShell one-liner querying Lxss registry keys and ext4.vhdx attributes.
# Must use only read-only cmdlets (Get-ItemProperty, Test-Path, Get-Item, Join-Path, ConvertTo-Json).
VHDX_PS_COMMAND: str = (
    "Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss\\*' "
    "-ErrorAction SilentlyContinue | ForEach-Object { "
    "$path = Join-Path $_.BasePath 'ext4.vhdx'; "
    "if (Test-Path $path) { "
    "[PSCustomObject]@{"
    "Distribution=$_.DistributionName; "
    "Path=$path; "
    "Bytes=(Get-Item $path).Length; "
    "Sparse=((Get-Item $path).Attributes -band [IO.FileAttributes]::SparseFile) -ne 0"
    "} "
    "} "
    "} | ConvertTo-Json"
)


async def query_vhdx_files() -> list[dict[str, Any]]:
    """Query WSL ext4.vhdx files and sparse flags via PowerShell.

    Returns an empty list when PowerShell is unavailable, exits non-zero,
    produces empty output, or on any error.
    """
    powershell = shutil.which("powershell.exe") or "powershell"
    if not shutil.which(powershell):
        return []

    cmd = [powershell, "-NoProfile", "-NonInteractive", "-Command", VHDX_PS_COMMAND]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            log.warning("PowerShell VHDX query returned non-zero exit code %d: %s", result.returncode, result.stderr)
            return []
        stdout = result.stdout.strip()
        if not stdout:
            return []
        data = json.loads(stdout)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to query WSL VHDX files: %s", exc)
        return []


def _clean_val(val: str | None) -> str | None:
    """Normalize systemctl property values, mapping empty, 'n/a', or '0' to None."""
    if val is None:
        return None
    cleaned = val.strip()
    if not cleaned or cleaned.lower() == "n/a" or cleaned == "0":
        return None
    return cleaned


def _parse_kv(text: str) -> dict[str, str]:
    """``KEY=VALUE`` lines (``systemctl show`` output) as a dict; other lines are ignored."""
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep:
            pairs[key.strip()] = value.strip()
    return pairs


def parse_fstrim(timer_show: str, service_show: str) -> dict[str, Any]:
    """Parse systemctl show output for fstrim.timer and fstrim.service.

    Inputs are stdout of:
      `systemctl show fstrim.timer -p ActiveState -p LastTriggerUSec`
      `systemctl show fstrim.service -p Result -p ExecMainExitTimestamp`
    """
    timer_kvs = _parse_kv(timer_show)
    service_kvs = _parse_kv(service_show)
    raw_active = _clean_val(timer_kvs.get("ActiveState"))
    timer_active = None if raw_active is None else raw_active == "active"

    last_trigger = _clean_val(timer_kvs.get("LastTriggerUSec"))
    if last_trigger is None:
        last_trigger = _clean_val(service_kvs.get("ExecMainExitTimestamp"))

    last_result = _clean_val(service_kvs.get("Result"))

    return {
        "timer_active": timer_active,
        "last_trigger": last_trigger,
        "last_result": last_result,
    }


def query_fstrim() -> dict[str, Any]:
    """Query systemd fstrim timer and service status. Never raises."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return {"timer_active": None, "last_trigger": None, "last_result": None}

    timer_out = ""
    service_out = ""

    try:
        res_timer = subprocess.run(
            [systemctl, "show", "fstrim.timer", "-p", "ActiveState", "-p", "LastTriggerUSec"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_timer.returncode == 0:
            timer_out = res_timer.stdout
        else:
            log.warning("systemctl show fstrim.timer failed with code %d: %s", res_timer.returncode, res_timer.stderr)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to query fstrim.timer: %s", exc)

    try:
        res_service = subprocess.run(
            [systemctl, "show", "fstrim.service", "-p", "Result", "-p", "ExecMainExitTimestamp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_service.returncode == 0:
            service_out = res_service.stdout
        else:
            log.warning(
                "systemctl show fstrim.service failed with code %d: %s",
                res_service.returncode,
                res_service.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to query fstrim.service: %s", exc)

    return parse_fstrim(timer_out, service_out)


def build_disk_status(
    vhdx_files: list[dict[str, Any]],
    *,
    current_distro: str | None,
    fs_used_bytes: int | None,
    fs_total_bytes: int | None,
    fstrim: dict[str, Any],
) -> dict[str, Any]:
    """Build structured WSL disk status from queried VHDX files and system metrics. PURE."""
    assert fs_used_bytes is None or fs_used_bytes >= 0, "fs_used_bytes must be None or >= 0"  # noqa: S101
    assert fs_total_bytes is None or fs_total_bytes >= 0, "fs_total_bytes must be None or >= 0"  # noqa: S101

    distributions: list[dict[str, Any]] = []
    for item in vhdx_files:
        raw_name = item.get("Distribution") if item.get("Distribution") is not None else item.get("distribution")
        name = str(raw_name or "")

        raw_path = item.get("Path") if item.get("Path") is not None else item.get("path")
        path = str(raw_path or "")

        raw_bytes = item.get("Bytes") if item.get("Bytes") is not None else item.get("bytes")
        vhdx_bytes: int | None = int(raw_bytes) if raw_bytes is not None else None

        raw_sparse = item.get("Sparse") if item.get("Sparse") is not None else item.get("sparse")
        sparse: bool | None = bool(raw_sparse) if raw_sparse is not None else None

        is_current = bool(current_distro) and (name == current_distro)

        entry_fs_used = fs_used_bytes if is_current else None
        entry_fs_total = fs_total_bytes if is_current else None

        slack_bytes: int | None = None
        if vhdx_bytes is not None and entry_fs_used is not None:
            diff = vhdx_bytes - entry_fs_used
            slack_bytes = diff if diff > 0 else 0

        findings: list[str] = []
        if sparse is False:
            findings.append("not_sparse")
        if is_current and fstrim.get("timer_active") is False:
            findings.append("fstrim_timer_inactive")

        distributions.append(
            {
                "name": name,
                "path": path,
                "vhdx_bytes": vhdx_bytes,
                "sparse": sparse,
                "is_current": is_current,
                "fs_used_bytes": entry_fs_used,
                "fs_total_bytes": entry_fs_total,
                "slack_bytes": slack_bytes,
                "findings": findings,
            }
        )

    for dist in distributions:
        slack = dist["slack_bytes"]
        assert slack is None or slack >= 0, "slack_bytes must be None or >= 0"  # noqa: S101

    return {
        "distributions": distributions,
        "fstrim": fstrim,
        "current_distro": current_distro,
    }


async def collect_disk_status() -> dict[str, Any]:
    """Collect live WSL disk status across Windows host and WSL environment."""
    current_distro = os.environ.get("WSL_DISTRO_NAME")
    fs_used_bytes: int | None = None
    fs_total_bytes: int | None = None

    if current_distro:
        try:
            usage = shutil.disk_usage("/")
            fs_used_bytes = usage.used
            fs_total_bytes = usage.total
        except OSError as exc:
            log.warning("Failed to query filesystem usage for /: %s", exc)
            fs_used_bytes = None
            fs_total_bytes = None

    vhdx_files = await query_vhdx_files()
    fstrim = await asyncio.to_thread(query_fstrim)

    status = build_disk_status(
        vhdx_files,
        current_distro=current_distro,
        fs_used_bytes=fs_used_bytes,
        fs_total_bytes=fs_total_bytes,
        fstrim=fstrim,
    )
    status["generated_at"] = datetime.now(UTC).isoformat()
    return status
