"""Credentials probe router.

Read-only endpoint that checks which AI tool CLIs and API keys are available
on the host. Never exposes secret values — only presence/connectivity state.
"""

from __future__ import annotations

import datetime as _dt_mod
import logging
import os
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

router = APIRouter(prefix="/api", tags=["credentials"])

log = logging.getLogger("dashboard.credentials")


def _env_present(key: str) -> bool:
    val = os.environ.get(key, "")
    return bool(val and val.strip())


def _env_source(key: str) -> str:
    return "env_var" if os.environ.get(key, "") else "unavailable"


def _require_local_request(request: Request) -> None:
    """Enforce that the request originates from localhost (issue #45)."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403, detail="This endpoint is only accessible locally"
        )


@router.get("/credentials")
async def get_credentials(request: Request) -> dict:
    """Probe provider credential and connectivity state. Never exposes secret values."""
    _require_local_request(request)
    probes: list[dict] = []

    # GitHub CLI
    gh_binary = shutil.which("gh")
    gh_auth_ok = False
    gh_auth_detail = "gh not found"
    if gh_binary:
        try:
            _excluded = {"SECRET", "PASSWORD", "ANTHROPIC_API_KEY", "DASHBOARD_API_KEY"}
            _safe_env = {
                k: v
                for k, v in os.environ.items()
                if not any(exc in k.upper() for exc in _excluded)
            }
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env=_safe_env,
            )
            gh_auth_ok = result.returncode == 0
            gh_auth_detail = "authenticated" if gh_auth_ok else "not logged in"
        except Exception:
            gh_auth_detail = "probe failed"

    probes.append(
        {
            "id": "github_cli",
            "label": "GitHub CLI",
            "icon": "github",
            "installed": gh_binary is not None,
            "authenticated": gh_auth_ok,
            "reachable": gh_auth_ok,
            "usable": gh_auth_ok,
            "status": (
                "ready"
                if gh_auth_ok
                else ("not_authed" if gh_binary else "not_installed")
            ),
            "detail": gh_auth_detail,
            "config_source": "system" if gh_binary else "unavailable",
            "docs_url": "https://cli.github.com/",
            "setup_hint": "Run: gh auth login",
        }
    )

    # Jules CLI
    jules_binary = shutil.which("jules")
    probes.append(
        {
            "id": "jules_cli",
            "label": "Jules CLI",
            "icon": "jules",
            "installed": jules_binary is not None,
            "authenticated": jules_binary is not None,
            "reachable": jules_binary is not None,
            "usable": jules_binary is not None,
            "status": "ready" if jules_binary else "not_installed",
            "detail": (
                f"Found at {jules_binary}"
                if jules_binary
                else "jules not found on PATH"
            ),
            "config_source": "system" if jules_binary else "unavailable",
            "docs_url": "https://jules.google/docs/",
            "setup_hint": "Install Jules CLI from jules.google",
        }
    )

    # Jules API
    jules_api_key = _env_present("JULES_API_KEY") or _env_present("GOOGLE_API_KEY")
    probes.append(
        {
            "id": "jules_api",
            "label": "Jules API",
            "icon": "jules",
            "installed": True,
            "authenticated": jules_api_key,
            "reachable": jules_api_key,
            "usable": jules_api_key,
            "status": "ready" if jules_api_key else "missing_key",
            "detail": (
                "API key present"
                if jules_api_key
                else "JULES_API_KEY or GOOGLE_API_KEY not set"
            ),
            "config_source": (
                _env_source("JULES_API_KEY") if jules_api_key else "unavailable"
            ),
            "docs_url": "https://jules.google/docs/api/",
            "setup_hint": "Set JULES_API_KEY environment variable",
        }
    )

    # Codex CLI
    codex_binary = shutil.which("codex")
    openai_key = _env_present("OPENAI_API_KEY")
    probes.append(
        {
            "id": "codex_cli",
            "label": "Codex CLI",
            "icon": "openai",
            "installed": codex_binary is not None,
            "authenticated": openai_key,
            "reachable": codex_binary is not None and openai_key,
            "usable": codex_binary is not None and openai_key,
            "status": (
                "ready"
                if (codex_binary and openai_key)
                else ("missing_key" if codex_binary else "not_installed")
            ),
            "detail": (
                "Ready"
                if (codex_binary and openai_key)
                else (
                    "OPENAI_API_KEY not set"
                    if codex_binary
                    else "codex not found on PATH"
                )
            ),
            "config_source": (
                _env_source("OPENAI_API_KEY")
                if openai_key
                else ("system" if codex_binary else "unavailable")
            ),
            "docs_url": "https://github.com/openai/codex",
            "setup_hint": "npm install -g @openai/codex && set OPENAI_API_KEY",
        }
    )

    # Claude Code CLI
    claude_binary = shutil.which("claude")
    anthropic_key = _env_present("ANTHROPIC_API_KEY")
    probes.append(
        {
            "id": "claude_code_cli",
            "label": "Claude Code CLI",
            "icon": "anthropic",
            "installed": claude_binary is not None,
            "authenticated": anthropic_key,
            "reachable": claude_binary is not None and anthropic_key,
            "usable": claude_binary is not None and anthropic_key,
            "status": (
                "ready"
                if (claude_binary and anthropic_key)
                else ("missing_key" if claude_binary else "not_installed")
            ),
            "detail": (
                "Ready"
                if (claude_binary and anthropic_key)
                else (
                    "ANTHROPIC_API_KEY not set"
                    if claude_binary
                    else "claude not found on PATH"
                )
            ),
            "config_source": (
                _env_source("ANTHROPIC_API_KEY")
                if anthropic_key
                else ("system" if claude_binary else "unavailable")
            ),
            "docs_url": "https://docs.anthropic.com/claude-code",
            "setup_hint": "npm install -g @anthropic-ai/claude-code && set ANTHROPIC_API_KEY",
        }
    )

    # Cline (VS Code extension)
    cline_config = (
        Path.home()
        / ".config"
        / "Code"
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev"
    )
    cline_installed = cline_config.exists()
    probes.append(
        {
            "id": "cline",
            "label": "Cline (VS Code)",
            "icon": "vscode",
            "installed": cline_installed,
            "authenticated": cline_installed,
            "reachable": cline_installed,
            "usable": cline_installed,
            "status": "ready" if cline_installed else "not_installed",
            "detail": (
                "VS Code extension data found"
                if cline_installed
                else "Cline VS Code extension not found"
            ),
            "config_source": "vscode" if cline_installed else "unavailable",
            "docs_url": "https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev",
            "setup_hint": "Install Cline extension in VS Code",
        }
    )

    # Ollama
    ollama_binary = shutil.which("ollama")
    probes.append(
        {
            "id": "ollama_local",
            "label": "Ollama (Local)",
            "icon": "ollama",
            "installed": ollama_binary is not None,
            "authenticated": True,
            "reachable": ollama_binary is not None,
            "usable": ollama_binary is not None,
            "status": "ready" if ollama_binary else "not_installed",
            "detail": (
                f"Found at {ollama_binary}"
                if ollama_binary
                else "ollama not found on PATH"
            ),
            "config_source": "system" if ollama_binary else "unavailable",
            "docs_url": "https://ollama.com/",
            "setup_hint": "Install from ollama.com",
        }
    )

    ready = sum(1 for p in probes if p["usable"])
    return {
        "probes": probes,
        "summary": {
            "total": len(probes),
            "ready": ready,
            "not_ready": len(probes) - ready,
        },
        "probed_at": datetime.now(UTC).isoformat(),
    }
