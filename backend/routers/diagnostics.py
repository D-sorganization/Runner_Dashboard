"""Dashboard diagnostics routes.

Extracted from server.py (issue #360).
Routes:
  GET  /api/diagnostics/summary
  POST /api/diagnostics/restart-service
  POST /api/launchers/generate
  GET  /api/runner-routing-audit
  POST /api/runner-routing-audit/refresh
  GET  /api/diagnostics/artifact
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from identity import require_scope

log = logging.getLogger("dashboard.diagnostics")
router = APIRouter(tags=["diagnostics"])

# ---------------------------------------------------------------------------
# Global Caches for Diagnostics
# ---------------------------------------------------------------------------
_last_wsl_vhdx_status: list[dict[str, Any]] = []
_last_storage_handle_incident: dict[str, Any] = {
    "detected": False,
    "error_code": None,
    "target_file": None,
    "message": None,
}


def get_cached_wsl_vhdx_status() -> list[dict[str, Any]]:
    """Return the cached WSL VHDX attachment status list."""
    return _last_wsl_vhdx_status


def get_cached_storage_handle_incident() -> dict[str, Any]:
    """Return the cached storage-handle incident details."""
    return _last_storage_handle_incident


async def query_wsl_vhdx_status() -> list[dict[str, Any]]:
    """Query VHDX attachment status using Get-DiskImage via powershell if available."""
    wsl_vhdx_status: list[dict[str, Any]] = []
    powershell_bin = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell_bin:
        ps_cmd = (
            "Get-ItemProperty -Path "
            "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss\\*' "
            "-ErrorAction SilentlyContinue | ForEach-Object { "
            "  $path = Join-Path $_.BasePath 'ext4.vhdx'; "
            "  if (Test-Path $path) { "
            "    $attached = (Get-DiskImage -ImagePath $path "
            "      -ErrorAction SilentlyContinue).Attached; "
            "    [PSCustomObject]@{Distribution=$_.DistributionName; "
            "      Path=$path; Attached=$attached} "
            "  } "
            "} | ConvertTo-Json"
        )
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [powershell_bin, "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    wsl_vhdx_status = [data]
                elif isinstance(data, list):
                    wsl_vhdx_status = data
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to query WSL VHDX attachment: %s", exc)
    return wsl_vhdx_status


def detect_sharing_violations(
    wsl_vhdx_status: list[dict[str, Any]] | None = None,
    wsl_status_str: str = "",
) -> dict[str, Any]:
    """Detect sharing violations (ERROR_SHARING_VIOLATION) on DBs or Stopped WSL VHDX files."""
    storage_incident: dict[str, Any] = {
        "detected": False,
        "error_code": None,
        "target_file": None,
        "message": None,
    }

    # Check databases
    db_paths = [
        Path.home() / "actions-runners" / "dashboard" / "replay.db",
        Path.home() / "actions-runners" / "dashboard" / "push.db",
    ]
    for db_path in db_paths:
        if db_path.exists():
            try:
                # Try opening the file to check for sharing violation
                with open(db_path, "a"):
                    pass
            except PermissionError as exc:
                if getattr(exc, "winerror", None) == 32 or "sharing violation" in str(exc).lower():
                    storage_incident = {
                        "detected": True,
                        "error_code": "ERROR_SHARING_VIOLATION",
                        "target_file": str(db_path),
                        "message": (
                            f"Storage handle conflict: {db_path.name} is locked "
                            f"by another process (ERROR_SHARING_VIOLATION)."
                        ),
                    }
                    return storage_incident
            except Exception:  # noqa: BLE001
                pass

    # Check Stopped WSL VHDX files
    if wsl_vhdx_status:
        wsl_status_lower = wsl_status_str.lower()
        for item in wsl_vhdx_status:
            vhdx_path_str = item.get("Path") or item.get("path")
            if not vhdx_path_str:
                continue
            vhdx_path = Path(vhdx_path_str)
            if vhdx_path.exists():
                try:
                    with open(vhdx_path, "rb"):
                        pass
                except PermissionError as exc:
                    if getattr(exc, "winerror", None) == 32 or "sharing violation" in str(exc).lower():
                        is_running = False
                        distro_name = str(item.get("Distribution", "")).lower()
                        if distro_name and distro_name in wsl_status_lower:
                            for line in wsl_status_lower.splitlines():
                                if distro_name in line and "running" in line:
                                    is_running = True
                                    break
                        if not is_running:
                            distro_name_err = item.get("Distribution")
                            storage_incident = {
                                "detected": True,
                                "error_code": "ERROR_SHARING_VIOLATION",
                                "target_file": str(vhdx_path),
                                "message": (
                                    f"Storage handle conflict: VHDX file {vhdx_path.name} "
                                    f"for distribution '{distro_name_err}' is locked by "
                                    f"another process (ERROR_SHARING_VIOLATION) despite "
                                    f"not running."
                                ),
                            }
                            return storage_incident
                except Exception:  # noqa: BLE001
                    pass

    return storage_incident


def generate_markdown_artifact_content(summary: dict[str, Any]) -> str:
    """Generate Markdown diagnostics artifact suitable for attaching to issues/PRs."""
    lines = []
    lines.append("# Runner Dashboard Diagnostics Artifact")
    lines.append(f"Generated: {datetime.now(UTC).isoformat()}")
    lines.append(f"Hostname: {summary.get('hostname', 'unknown')}")
    lines.append(f"OS Platform: {sys.platform}")
    lines.append("")

    # WSL Status
    lines.append("## WSL Status")
    lines.append(f"- Available: `{summary.get('wsl_available', False)}`")
    lines.append("```")
    lines.append(summary.get("wsl_status", "WSL not available").strip())
    lines.append("```")
    lines.append("")

    # WSL VHDX Status
    lines.append("## WSL VHDX Status")
    wsl_vhdx = summary.get("wsl_vhdx_status", [])
    if wsl_vhdx:
        for idx, item in enumerate(wsl_vhdx):
            lines.append(f"### Distribution {idx + 1}: {item.get('Distribution', 'unknown')}")
            lines.append(f"- Path: `{item.get('Path', 'unknown')}`")
            lines.append(f"- Attached: `{item.get('Attached')}`")
    else:
        lines.append("No WSL VHDX files detected or Windows host diagnostics unavailable.")
    lines.append("")

    # Storage Handle Incident
    lines.append("## Storage Incident Detection")
    incident = summary.get("storage_handle_incident", {})
    if incident.get("detected"):
        lines.append("### ⚠️ STORAGE HANDLE INCIDENT DETECTED")
        lines.append(f"- Error Code: `{incident.get('error_code')}`")
        lines.append(f"- Target File: `{incident.get('target_file')}`")
        lines.append(f"- Message: {incident.get('message')}")
    else:
        lines.append("No active storage-handle incidents or sharing violations detected.")
    lines.append("")

    # Process Information
    lines.append("## Dashboard Process Info")
    lines.append(f"- PID: `{summary.get('dashboard_pid', 'unknown')}`")
    lines.append(f"- Memory usage: `{summary.get('dashboard_memory_mb', 'unknown')} MB`")
    lines.append(f"- Dashboard port: `{summary.get('dashboard_port', 'unknown')}`")
    lines.append("")

    # Git
    lines.append("## Git Metadata")
    lines.append(f"- Commit: `{summary.get('git_commit', 'unknown')}`")
    lines.append(f"- Drifted: `{summary.get('is_drifted', False)}`")
    if summary.get("drift_details"):
        lines.append("### Drift Details")
        lines.append("```")
        lines.append(str(summary.get("drift_details")).strip())
        lines.append("```")
    lines.append("")

    # Environment Variables
    lines.append("## Sanitized Environment Variables")
    lines.append("```")
    for k, v in sorted(os.environ.items()):
        if any(sec in k.upper() for sec in ["TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH", "CRED", "PAT", "JWT"]):
            lines.append(f"{k}=********")
        else:
            lines.append(f"{k}={v}")
    lines.append("```")
    lines.append("")

    # Compact Flow Runbook Reference
    lines.append("## Compact Flow Runbook Reference")
    lines.append(
        "To safely compact a WSL VHDX, refer to [docs/runbooks/wsl-vhdx-compaction.md](file:///docs/runbooks/wsl-vhdx-compaction.md):"
    )
    lines.append("1. Disable monitor tasks: `Disable-ScheduledTask -TaskName 'WSL-Dashboard-Keepalive'`")
    lines.append("2. Stop WSL services: `wsl --shutdown` and `Stop-Service -Name 'LxssManager'`")
    lines.append("3. Dismount disk image using `diskpart` or `Dismount-VHD`")
    lines.append("4. Compact VHDX using `diskpart` (compact vdisk) or `Optimize-VHD`")
    lines.append("5. Restart LxssManager service: `Start-Service -Name 'LxssManager'`")
    lines.append("6. Re-enable monitor tasks: `Enable-ScheduledTask -TaskName 'WSL-Dashboard-Keepalive'`")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_get_git_drift: Any = None
PORT: int = 8321
SYSTEMCTL_BIN: str = "/usr/bin/systemctl"

HOSTED_RUNNER_PATTERNS = re.compile(
    r"^(ubuntu-|windows-|macos-|GitHub Actions \d|Hosted Agent)",
    re.IGNORECASE,
)
_runner_audit_cache: dict[str, Any] = {
    "violations": [],
    "last_checked": None,
    "error": None,
}
_runner_audit_lock = asyncio.Lock()
_run_runner_audit_fn: Any = None


def set_dependencies(  # type: ignore[no-untyped-def]
    *,
    get_git_drift,
    port: int,
    systemctl_bin: str,
    run_runner_audit_fn,
) -> None:
    """Inject server-level singletons (called from server.py)."""
    global _get_git_drift, PORT, SYSTEMCTL_BIN, _run_runner_audit_fn
    _get_git_drift = get_git_drift
    PORT = port
    SYSTEMCTL_BIN = systemctl_bin
    _run_runner_audit_fn = run_runner_audit_fn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/diagnostics/summary")
async def get_diagnostics_summary() -> dict:
    """Consolidated diagnostics for the Diagnostics tab."""
    global _last_wsl_vhdx_status, _last_storage_handle_incident
    import psutil

    summary: dict[str, Any] = {}

    # WSL status
    try:
        wsl_result = await asyncio.to_thread(
            subprocess.run,
            ["wsl", "-l", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-16-le",
            errors="replace",
        )
        summary["wsl_status"] = wsl_result.stdout.strip()
        summary["wsl_available"] = wsl_result.returncode == 0
    except (OSError, subprocess.SubprocessError, TimeoutError, UnicodeDecodeError):  # noqa: BLE001
        try:
            wsl_result_raw = await asyncio.to_thread(
                subprocess.run,
                ["wsl", "-l", "-v"],
                capture_output=True,
                timeout=10,
            )
            summary["wsl_status"] = wsl_result_raw.stdout.decode("utf-16-le", errors="replace").strip()
            summary["wsl_available"] = wsl_result_raw.returncode == 0
        except (OSError, subprocess.SubprocessError, TimeoutError, UnicodeDecodeError):  # noqa: BLE001
            summary["wsl_status"] = "WSL not available"
            summary["wsl_available"] = False

    # VHDX attachment status check (using PowerShell Get-DiskImage if available)
    wsl_vhdx_status = await query_wsl_vhdx_status()
    summary["wsl_vhdx_status"] = wsl_vhdx_status
    _last_wsl_vhdx_status = wsl_vhdx_status

    # Storage Incident detection (ERROR_SHARING_VIOLATION)
    storage_incident = detect_sharing_violations(wsl_vhdx_status, str(summary.get("wsl_status", "")))
    summary["storage_handle_incident"] = storage_incident
    _last_storage_handle_incident = storage_incident

    # Dashboard process info
    proc = psutil.Process(os.getpid())
    summary["dashboard_pid"] = proc.pid
    summary["dashboard_memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
    summary["dashboard_port"] = PORT

    # Git commit
    try:
        out = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent.parent.parent,
        )
        summary["git_commit"] = out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError, TimeoutError):  # noqa: BLE001
        summary["git_commit"] = "unknown"

    # Drift info
    try:
        drift = await _get_git_drift()
        summary["is_drifted"] = drift.get("is_drifted", False)
        summary["source_commit"] = drift.get("source_commit", "unknown")
        summary["remote_commit"] = drift.get("remote_commit", "unknown")
        summary["drift_details"] = drift.get("drift_details", "")
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        summary["is_drifted"] = False

    return summary


@router.get("/api/diagnostics/artifact")
async def get_diagnostics_artifact() -> Response:
    """Generate and return a markdown diagnostics artifact for PR/issue attachment."""
    summary = await get_diagnostics_summary()
    markdown_content = generate_markdown_artifact_content(summary)
    return Response(
        content=markdown_content,
        media_type="text/markdown",
        headers={"Content-Disposition": "attachment; filename=diagnostics-artifact.md"},
    )


@router.get("/api/github/status")
async def get_github_status() -> dict[str, Any]:
    """Return the last observed GitHub API health state for dashboard banners."""
    try:
        import gh_utils

        rate_limit_status = gh_utils.get_rate_limit_status()
        if rate_limit_status.get("status") == "rate_limited":
            return rate_limit_status

        import gh_client

        return gh_client.get_status()
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {
            "status": "unknown",
            "detail": f"GitHub API status unavailable: {exc}",
            "endpoint": "",
            "retry_after_seconds": 0,
            "updated_at": None,
        }


@router.post("/api/diagnostics/restart-service")
async def restart_dashboard_service(
    request: Request,
    *,
    principal: Any = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Restart the dashboard systemd service (WSL/Linux only, localhost only)."""
    client = request.client
    if not client or client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Local access only")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "sudo",
                "-n",
                "systemd-run",
                "--unit=runner-dashboard-self-restart",
                "--on-active=1",
                "--collect",
                "/bin/systemctl",
                "restart",
                "runner-dashboard.service",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout + result.stderr).strip(),
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to restart runner-dashboard service")
        raise HTTPException(status_code=500, detail="Restart failed") from exc


@router.post("/api/launchers/generate")
async def generate_launchers(
    request: Request,
    principal: Any = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Generate Windows PowerShell launcher scripts on the Desktop."""
    output_dir = Path.home() / "Desktop" / "RunnerDashboard"
    output_dir.mkdir(parents=True, exist_ok=True)

    launchers_created: list[str] = []

    script = output_dir / "Open-Dashboard.ps1"
    script.write_text('Start-Process "http://localhost:8321"\n', encoding="utf-8")
    launchers_created.append(str(script))

    keepalive = output_dir / "Start-WSL-Keepalive.ps1"
    keepalive.write_text(
        'Start-ScheduledTask -TaskName "WSL-Dashboard-Keepalive" -ErrorAction SilentlyContinue\n'
        'Write-Host "Keepalive task started"\n',
        encoding="utf-8",
    )
    launchers_created.append(str(keepalive))

    restart = output_dir / "Restart-Dashboard-Service.ps1"
    restart.write_text(
        'wsl -d Ubuntu -e bash -lc "sudo -n systemctl restart runner-dashboard.service && echo Service restarted"\n',
        encoding="utf-8",
    )
    launchers_created.append(str(restart))

    diag = output_dir / "Open-Diagnostics.ps1"
    diag.write_text('Start-Process "http://localhost:8321/#diagnostics"\n', encoding="utf-8")
    launchers_created.append(str(diag))

    log.info("Generated %d launcher scripts in %s", len(launchers_created), output_dir)
    return {
        "output_dir": str(output_dir),
        "launchers": launchers_created,
        "message": f"Created {len(launchers_created)} launcher scripts in {output_dir}",
    }


@router.get("/api/runner-routing-audit")
async def get_runner_routing_audit() -> JSONResponse:
    """Return recent workflow runs that executed on GitHub-hosted runners."""
    return JSONResponse(_runner_audit_cache)


@router.post("/api/runner-routing-audit/refresh")
async def refresh_runner_routing_audit() -> JSONResponse:
    """Trigger an immediate audit refresh."""
    if _run_runner_audit_fn is not None:
        asyncio.create_task(_run_runner_audit_fn())
    return JSONResponse({"status": "refresh triggered"})
