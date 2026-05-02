"""Dashboard diagnostics routes.

Extracted from server.py (issue #360).
Routes:
  GET  /api/diagnostics/summary
  POST /api/diagnostics/restart-service
  POST /api/launchers/generate
  GET  /api/runner-routing-audit
  POST /api/runner-routing-audit/refresh
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from identity import require_scope

log = logging.getLogger("dashboard.diagnostics")
router = APIRouter(tags=["diagnostics"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_get_git_drift = None
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
_run_runner_audit_fn = None


def set_dependencies(
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
    import psutil

    summary: dict[str, object] = {}

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


@router.post("/api/diagnostics/restart-service")
async def restart_dashboard_service(
    request: Request,
    *,
    principal=Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Restart the dashboard systemd service (WSL/Linux only, localhost only)."""
    client = request.client
    if not client or client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Local access only")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [SYSTEMCTL_BIN, "--user", "restart", "runner-dashboard"],
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
    principal=Depends(require_scope("system.control")),  # noqa: B008
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
        'wsl -e bash -c "systemctl --user restart runner-dashboard && echo Service restarted"\n',
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
