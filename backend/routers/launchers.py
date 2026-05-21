"""Windows PowerShell launcher script generation endpoint.

Extracted from server.py (issue #2942).

Route
-----
POST /api/launchers/generate — write launcher .ps1 scripts to the Desktop
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from identity import Principal, require_scope  # noqa: B008

log = logging.getLogger("dashboard")

router = APIRouter(tags=["launchers"])


@router.post("/api/launchers/generate")
async def generate_launchers(
    request: Request,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Generate Windows PowerShell launcher scripts on the Desktop.

    Precondition: caller has system.control scope.
    Postcondition: result has 'output_dir', 'launchers', 'message' keys.
    """
    output_dir = Path.home() / "Desktop" / "RunnerDashboard"
    output_dir.mkdir(parents=True, exist_ok=True)
    launchers_created: list[str] = []

    scripts: list[tuple[str, str]] = [
        ("Open-Dashboard.ps1", 'Start-Process "http://localhost:8321"\n'),
        (
            "Start-WSL-Keepalive.ps1",
            'Start-ScheduledTask -TaskName "WSL-Dashboard-Keepalive" -ErrorAction SilentlyContinue\n'
            'Write-Host "Keepalive task started"\n',
        ),
        (
            "Restart-Dashboard-Service.ps1",
            'wsl -e bash -c "systemctl --user restart runner-dashboard && echo Service restarted"\n',
        ),
        ("Open-Diagnostics.ps1", 'Start-Process "http://localhost:8321/#diagnostics"\n'),
    ]
    for name, content in scripts:
        path = output_dir / name
        path.write_text(content, encoding="utf-8")
        launchers_created.append(str(path))

    log.info("Generated %d launcher scripts in %s", len(launchers_created), output_dir)
    assert launchers_created, "at least one launcher must be created"
    return {
        "output_dir": str(output_dir),
        "launchers": launchers_created,
        "message": f"Created {len(launchers_created)} launcher scripts in {output_dir}",
    }
