"""Command execution and GitHub API utilities extracted from server.py (issue #299).

Provides:
- Async subprocess execution (run_cmd)
- GitHub API wrappers (gh_api, gh_api_raw)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    pass


# ─── Subprocess Execution ────────────────────────────────────────────────────


async def run_cmd(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a shell command asynchronously.

    Args:
        cmd: Command as list of args (no shell interpretation)
        timeout: Command timeout in seconds (default 30)
        cwd: Working directory

    Returns:
        Tuple of (return_code, stdout, stderr)
        Return code 127 indicates command not found
        Return code -1 indicates timeout
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode(),
            stderr.decode(),
        )
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        proc.kill()
        return -1, "", "Command timed out"


# ─── GitHub API Wrappers ────────────────────────────────────────────────────


async def gh_api(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI.

    Uses GH_TOKEN env var when set (required for admin:org endpoints such as
    /orgs/{org}/actions/runners).  GH_TOKEN must be a classic PAT with
    scopes: repo, admin:org.  See docs/operations/fleet-machine-setup.md.

    Args:
        endpoint: API endpoint path (e.g., "/orgs/my-org/actions/runners")

    Returns:
        Parsed JSON response

    Raises:
        HTTPException: If the API call fails
    """
    code, stdout, stderr = await run_cmd(["gh", "api", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return json.loads(stdout)


# gh_api_admin is an alias kept for call-site clarity; all calls use GH_TOKEN.
gh_api_admin = gh_api


async def gh_api_raw(endpoint: str) -> str:
    """Call the GitHub API via gh CLI and return the raw body text.

    Args:
        endpoint: API endpoint path

    Returns:
        Raw response text (not JSON-parsed)

    Raises:
        HTTPException: If the API call fails
    """
    code, stdout, stderr = await run_cmd(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return stdout
