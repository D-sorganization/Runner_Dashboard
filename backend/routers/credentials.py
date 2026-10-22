"""Credentials probe router.

Exposes read-only probe endpoints for subscription CLI login state on the
dashboard node. Pay-per-use API keys are not accepted or written from the UI.

Only accessible from localhost.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from cache_utils import cache_get, cache_set
from dashboard_config import CacheTtl
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from routers.linear import list_workspace_summaries
from security import safe_subprocess_env

# Windows-only subprocess flag that prevents a new GUI window from appearing
# when invoking a console-attached executable. On non-Windows it is unused.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _resolve_vscode_cli() -> str | None:
    """Return the path to the VS Code *CLI shim* (or None if not installed).

    On Windows, ``shutil.which("code")`` resolves to ``Code.exe`` (the GUI
    binary). Invoking ``Code.exe --list-extensions`` spawns a new blank
    editor window every time — a known VS Code quirk. The headless shim is
    ``code.cmd`` (alongside ``Code.exe`` in the install directory). We
    prefer the shim and fall back to whatever ``shutil.which`` finds.

    Tracking: chatgpt-codex-connector→Jules feedback loop investigation,
    where the dashboard frontend polled credential status every few seconds
    and each poll opened a new blank VS Code window.
    """
    if sys.platform == "win32":
        # code.cmd is the CLI; Code.exe is the GUI. Look for the shim first.
        cli = shutil.which("code.cmd")
        if cli:
            return cli
    return shutil.which("code")


async def _vscode_has_extension(extension_id: str) -> bool | None:
    """Best-effort check whether *extension_id* is installed in VS Code.

    Returns:
        True  — extension confirmed installed via ``code --list-extensions``.
        False — VS Code CLI is present and the extension is not listed.
        None  — VS Code CLI not available (caller should fall back to other signals).

    On Windows, runs the binary with ``CREATE_NO_WINDOW`` so even an
    accidental GUI resolution (e.g. an old install where ``code.cmd`` was
    never deployed) does not spawn a visible window.
    """
    cli = _resolve_vscode_cli()
    if not cli:
        return None
    if not _should_probe_vscode_cli(cli):
        return None

    def _run_headless() -> subprocess.CompletedProcess[str]:
        # Wrapper so the closure carries every kwarg as a real argument,
        # which keeps mypy from having to type-check **kwargs through
        # asyncio.to_thread → subprocess.run.
        if sys.platform == "win32":
            return subprocess.run(  # noqa: S603 — fixed-arg, trusted binary
                [cli, "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=_CREATE_NO_WINDOW,
            )
        return subprocess.run(  # noqa: S603 — fixed-arg, trusted binary
            [cli, "--list-extensions"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

    try:
        result = await asyncio.to_thread(_run_headless)
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.warning("VS Code extension probe failed for extension_id=%s: %s", extension_id, type(exc).__name__)
        return None
    return extension_id in (result.stdout or "")


UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

router = APIRouter(prefix="/api", tags=["credentials"])

log = logging.getLogger("dashboard.credentials")

# env-file paths
_MAXWELL_ENV = Path.home() / ".config" / "maxwell-daemon" / "env"
_DASHBOARD_ENV = Path.home() / ".config" / "runner-dashboard" / "env"


def _env_present(key: str) -> bool:
    val = os.environ.get(key, "")
    return bool(val and val.strip())


def _env_present_anywhere(key: str) -> bool:
    """Check env var in current process AND in maxwell/runner env files."""
    if _env_present(key):
        return True
    for env_file in (_MAXWELL_ENV, _DASHBOARD_ENV):
        if not env_file.exists():
            continue
        try:
            text = env_file.read_text(encoding="utf-8")
            if re.search(rf"^{re.escape(key)}=\S", text, re.MULTILINE):
                return True
        except (OSError, UnicodeDecodeError) as exc:
            log.debug("Failed reading env file %s for key=%s: %s", env_file, key, type(exc).__name__)
    return False


def _cli_login_file_present(env_var: str, default_home_subdir: str, filename: str) -> bool:
    """Return whether the CLI's own subscription-login file exists.

    Codex CLI (``codex login``) writes ``auth.json`` under ``$CODEX_HOME``
    (default ``~/.codex``); Claude Code CLI (``claude login``) writes
    ``.credentials.json`` under ``$CLAUDE_CONFIG_DIR`` (default ``~/.claude``).
    Only existence is checked -- the file is never opened, read, or logged.
    """
    base = os.environ.get(env_var, "").strip()
    login_dir = Path(base) if base else Path.home() / default_home_subdir
    return (login_dir / filename).exists()


def _find_binary(name: str) -> str | None:
    """Search for a binary on PATH and in common installation locations."""
    # First: PATH lookup
    found = shutil.which(name)
    if found:
        return found

    # Common static paths
    static_paths = [
        Path.home() / ".npm-global" / "bin" / name,
        Path.home() / ".local" / "bin" / name,
        Path("/usr/local/bin") / name,
        Path.home() / ".cargo" / "bin" / name,
    ]

    # fnm paths: ~/.local/share/fnm/node-versions/*/installation/bin/<name>
    _fnm_base = Path.home() / ".local" / "share" / "fnm" / "node-versions"
    if _fnm_base.exists():
        for version_dir in _fnm_base.iterdir():
            if version_dir.is_dir():
                candidate = version_dir / "installation" / "bin" / name
                static_paths.append(candidate)

    for p in static_paths:
        if p.exists():
            return str(p)

    return None


def _is_wsl() -> bool:
    return "microsoft" in platform.uname().release.lower()


def _should_probe_vscode_cli(binary: str | None) -> bool:
    """Return true only when invoking ``code`` is safe and explicitly allowed.

    On WSL, ``code`` usually resolves to the Windows VS Code bridge.  Even
    read-looking commands such as ``code --list-extensions`` can start the GUI,
    which caused repeated VS Code windows across runner hosts.  Keep the probe
    filesystem-only by default; operators can opt in for an interactive machine.
    """
    if not binary:
        return False
    if os.environ.get("RUNNER_DASHBOARD_ALLOW_VSCODE_CLI_PROBE", "").lower() in {"1", "true", "yes"}:
        return True
    normalized = binary.replace("\\", "/").lower()
    if _is_wsl() and (normalized.startswith("/mnt/") or normalized.endswith(".exe")):
        return False
    return not normalized.endswith(".exe")


async def _vscode_extension_installed(extension_id: str, binary: str | None) -> bool:
    if not _should_probe_vscode_cli(binary):
        return False
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [binary or "code", "--list-extensions"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.warning("VS Code CLI extension probe failed for extension_id=%s: %s", extension_id, type(exc).__name__)
        return False
    return extension_id in result.stdout


def _env_source(key: str) -> str:
    return "env_var" if os.environ.get(key, "") else "unavailable"


def _require_local_request(request: Request) -> None:
    """Enforce that the request originates from localhost (issue #45, #321).

    Rejects requests that include proxy-forwarding headers (X-Forwarded-For,
    X-Real-IP, Forwarded) even when the transport-layer peer is loopback,
    preventing header-based bypass of the loopback trust check.
    """
    # Reject any request that carries forwarding headers — an attacker behind a
    # reverse proxy could set X-Forwarded-For: 127.0.0.1 to spoof local origin.
    _PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")
    for header in _PROXY_HEADERS:
        if request.headers.get(header):
            raise HTTPException(
                status_code=403,
                detail="This endpoint is only accessible locally (proxy headers not allowed)",
            )

    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="This endpoint is only accessible locally")


async def _probe_github_cli() -> dict[str, Any]:
    """Probe GitHub CLI presence and auth status."""
    gh_binary = shutil.which("gh")
    gh_auth_ok = False
    gh_auth_detail = "gh not found"
    if gh_binary:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env=safe_subprocess_env(),
            )
            gh_auth_ok = result.returncode == 0
            gh_auth_detail = "authenticated" if gh_auth_ok else "not logged in"
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            log.warning("GitHub CLI probe failed: %s", type(exc).__name__)
            gh_auth_detail = "probe failed"  # subprocess / OS error; non-fatal probe failure

    return {
        "id": "github_cli",
        "label": "GitHub CLI",
        "icon": "github",
        "installed": gh_binary is not None,
        "authenticated": gh_auth_ok,
        "reachable": gh_auth_ok,
        "usable": gh_auth_ok,
        "status": ("ready" if gh_auth_ok else ("not_authed" if gh_binary else "not_installed")),
        "detail": gh_auth_detail,
        "config_source": "system" if gh_binary else "unavailable",
        "docs_url": "https://cli.github.com/",
        "setup_hint": "Run: gh auth login",
    }


def _probe_jules_cli() -> dict[str, Any]:
    """Probe Jules CLI presence."""
    jules_binary = shutil.which("jules")
    return {
        "id": "jules_cli",
        "label": "Jules CLI",
        "icon": "jules",
        "installed": jules_binary is not None,
        "authenticated": jules_binary is not None,
        "reachable": jules_binary is not None,
        "usable": jules_binary is not None,
        "status": "ready" if jules_binary else "not_installed",
        "detail": (f"Found at {jules_binary}" if jules_binary else "jules not found on PATH"),
        "config_source": "system" if jules_binary else "unavailable",
        "docs_url": "https://jules.google/docs/",
        "setup_hint": "Install Jules CLI from jules.google",
    }


def _probe_jules_api() -> dict[str, Any]:
    """Probe Jules API key presence."""
    jules_api_key = _env_present("JULES_API_KEY")
    return {
        "id": "jules_api",
        "label": "Jules API",
        "icon": "jules",
        "installed": True,
        "authenticated": jules_api_key,
        "reachable": jules_api_key,
        "usable": jules_api_key,
        "status": "ready" if jules_api_key else "missing_key",
        "detail": ("API key present" if jules_api_key else "JULES_API_KEY not set"),
        "config_source": (_env_source("JULES_API_KEY") if jules_api_key else "unavailable"),
        "docs_url": "https://jules.google/docs/api/",
        "setup_hint": "Set JULES_API_KEY in ~/.config/runner-dashboard/env",
    }


def _probe_codex_cli() -> dict[str, Any]:
    """Probe OpenAI Codex CLI presence and ChatGPT subscription login."""
    codex_binary = _find_binary("codex")
    codex_login = _cli_login_file_present("CODEX_HOME", ".codex", "auth.json")
    authenticated = codex_login
    if codex_login:
        auth_detail = "Ready (codex login)"
    else:
        auth_detail = "not signed in: run `codex login`"
    return {
        "id": "codex_cli",
        "label": "Codex CLI",
        "icon": "openai",
        "installed": codex_binary is not None,
        "authenticated": authenticated,
        "reachable": codex_binary is not None and authenticated,
        "usable": codex_binary is not None and authenticated,
        "binary_found": codex_binary is not None,
        "key_status": "oauth" if codex_login else "missing",
        "status": (
            "ready" if (codex_binary and authenticated) else ("missing_key" if codex_binary else "not_installed")
        ),
        "detail": (auth_detail if codex_binary else "codex not on PATH or npm-global"),
        "config_source": "system" if codex_binary else "unavailable",
        "docs_url": "https://github.com/openai/codex",
        "setup_hint": "npm install -g @openai/codex, then `codex login` (ChatGPT subscription)",
    }


def _claude_login_file_present() -> bool:
    from staff.cli_oauth_session import resolve_claude_config_dir  # noqa: PLC0415

    return (resolve_claude_config_dir() / ".credentials.json").is_file()


def _probe_claude_code_cli() -> dict[str, Any]:
    """Probe Anthropic Claude Code CLI presence and subscription OAuth login."""
    from staff.cli_oauth_session import claude_oauth_session_alive  # noqa: PLC0415

    claude_binary = shutil.which("claude")
    claude_login = _claude_login_file_present()
    oauth_alive, oauth_detail = claude_oauth_session_alive(claude_binary)
    authenticated = bool(claude_login and oauth_alive)
    if claude_login and oauth_alive:
        auth_detail = "Ready (claude login)"
    elif claude_login:
        auth_detail = oauth_detail or "OAuth session expired; run `claude auth login`"
    else:
        auth_detail = "not signed in: run `claude login`"
    return {
        "id": "claude_code_cli",
        "label": "Claude Code CLI",
        "icon": "anthropic",
        "installed": claude_binary is not None,
        "authenticated": authenticated,
        "reachable": claude_binary is not None and authenticated,
        "usable": claude_binary is not None and authenticated,
        "binary_found": claude_binary is not None,
        "key_status": "oauth" if authenticated else "missing",
        "status": (
            "ready" if (claude_binary and authenticated) else ("missing_key" if claude_binary else "not_installed")
        ),
        "detail": (auth_detail if claude_binary else "claude not found on PATH"),
        "config_source": "system" if claude_binary else "unavailable",
        "docs_url": "https://docs.anthropic.com/claude-code",
        "setup_hint": "npm install -g @anthropic-ai/claude-code, then `claude login`",
    }


def _resolve_cline_detail(
    by_path: bool,
    by_ext: bool,
    skipped: bool,
    vscode_found: bool,
    compatible_key: bool,
    installed: bool,
) -> str:
    """Build user-facing diagnostic detail for Cline extension."""
    if installed and compatible_key:
        return "VS Code extension + compatible API key found"
    if installed:
        return "VS Code extension installed; set ANTHROPIC_API_KEY or OPENAI_API_KEY"
    if by_path:
        return "VS Code extension installed (globalStorage)"
    if by_ext:
        return "VS Code extension installed (code --list-extensions)"
    if skipped:
        return "VS Code CLI probe skipped to avoid launching the desktop UI"
    if vscode_found:
        return "VS Code found but Cline not installed"
    return "VS Code not found"


async def _probe_cline() -> dict[str, Any]:
    """Probe Cline VS Code extension installation and API key configuration."""
    _cline_storage = Path.home() / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
    _cline_by_path = _cline_storage.exists()
    _vscode_binary = _resolve_vscode_cli()
    _cline_by_ext = False
    if _vscode_binary and not _cline_by_path:
        ext_check = await _vscode_has_extension("saoudrizwan.claude-dev")
        _cline_by_ext = bool(ext_check)
    cline_installed = _cline_by_path or _cline_by_ext
    vscode_cli_probe_skipped = _vscode_binary is not None and not _should_probe_vscode_cli(_vscode_binary)
    _cline_compatible_key = _env_present("ANTHROPIC_API_KEY") or _env_present("OPENAI_API_KEY")
    detail = _resolve_cline_detail(
        by_path=_cline_by_path,
        by_ext=_cline_by_ext,
        skipped=vscode_cli_probe_skipped,
        vscode_found=_vscode_binary is not None,
        compatible_key=_cline_compatible_key,
        installed=cline_installed,
    )
    return {
        "id": "cline",
        "label": "Cline (VS Code)",
        "icon": "vscode",
        "installed": cline_installed,
        "vscode_found": _vscode_binary is not None,
        "vscode_cli_probe_skipped": vscode_cli_probe_skipped,
        "compatible_key_set": _cline_compatible_key,
        "authenticated": cline_installed and _cline_compatible_key,
        "reachable": cline_installed,
        "usable": cline_installed and _cline_compatible_key,
        "status": (
            "ready"
            if (cline_installed and _cline_compatible_key)
            else ("extension_installed" if cline_installed else "not_installed")
        ),
        "detail": detail,
        "config_source": "vscode" if cline_installed else "unavailable",
        "docs_url": "https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev",
        "setup_hint": "Install Cline extension in VS Code: ext install saoudrizwan.claude-dev",
    }


def _gemini_oauth_present() -> bool:
    """True when the Gemini CLI holds a Google sign-in (``~/.gemini/oauth_creds.json``)."""
    home = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home())
    return (home / ".gemini" / "oauth_creds.json").is_file()


def _probe_gemini_cli() -> dict[str, Any]:
    """Probe Gemini CLI presence and Google OAuth sign-in (no API-key billing)."""
    gemini_binary = shutil.which("gemini")
    oauth = _gemini_oauth_present()
    return {
        "id": "gemini_cli",
        "label": "Gemini CLI",
        "icon": "google",
        "installed": gemini_binary is not None,
        "authenticated": oauth,
        "reachable": gemini_binary is not None and oauth,
        "usable": gemini_binary is not None and oauth,
        "binary_found": gemini_binary is not None,
        "key_status": "oauth" if oauth else "missing",
        "status": ("ready" if (gemini_binary and oauth) else ("missing_key" if gemini_binary else "not_installed")),
        "detail": (
            "Ready"
            if (gemini_binary and oauth)
            else ("not signed in: run `gemini` once" if gemini_binary else "gemini not found on PATH")
        ),
        "config_source": "system" if gemini_binary else "unavailable",
        "docs_url": "https://github.com/google-gemini/gemini-cli",
        "setup_hint": "npm install -g @google/gemini-cli, then run `gemini` once to sign in with Google",
    }


def _probe_ollama() -> dict[str, Any]:
    """Probe local Ollama binary presence."""
    ollama_binary = shutil.which("ollama")
    return {
        "id": "ollama",
        "label": "Ollama (Local)",
        "icon": "ollama",
        "installed": ollama_binary is not None,
        "authenticated": True,
        "reachable": ollama_binary is not None,
        "usable": ollama_binary is not None,
        "status": "ready" if ollama_binary else "not_installed",
        "detail": (f"Found at {ollama_binary}" if ollama_binary else "ollama not found on PATH"),
        "config_source": "system" if ollama_binary else "unavailable",
        "docs_url": "https://ollama.com/",
        "setup_hint": "Install from ollama.com",
    }


def _antigravity_login_present() -> bool:
    """True when Antigravity CLI holds a Google sign-in session."""
    return (
        _cli_login_file_present("ANTIGRAVITY_HOME", ".gemini", "settings.json")
        or _cli_login_file_present("ANTIGRAVITY_HOME", ".gemini/antigravity-cli", "installation_id")
        or _cli_login_file_present("ANTIGRAVITY_HOME", ".gemini/antigravity-cli", "jetski_state.pbtxt")
    )


def _antigravity_quota_status() -> tuple[bool, str]:
    """Check quota headroom for antigravity."""
    try:
        from staff import quota as quota_mod

        return quota_mod.headroom("antigravity", 100.0)
    except Exception:  # noqa: BLE001
        return True, ""


def _probe_antigravity_cli() -> dict[str, Any]:
    """Probe Antigravity (agy) CLI presence, authentication, and quota."""
    from staff.cli_oauth_session import antigravity_oauth_session_alive  # noqa: PLC0415

    agy_binary = _find_binary("agy")
    installed = agy_binary is not None
    login_present = _antigravity_login_present()
    oauth_alive, oauth_detail = antigravity_oauth_session_alive(agy_binary)
    authenticated = bool(login_present and oauth_alive)
    quota_available, quota_detail = _antigravity_quota_status()

    if not installed:
        status, detail = "not_installed", "agy not found on PATH"
    elif not authenticated:
        if login_present and not oauth_alive:
            status, detail = "missing_key", oauth_detail or "OAuth session expired; run `agy auth login`"
        else:
            status, detail = "missing_key", "not signed in: run `agy` or `agy auth login`"
    elif not quota_available:
        status, detail = "quota_exhausted", quota_detail or "antigravity quota exhausted"
    else:
        status, detail = "ready", "Ready"

    usable = installed and authenticated and quota_available
    return {
        "id": "antigravity",
        "label": "Antigravity CLI",
        "icon": "google",
        "installed": installed,
        "authenticated": authenticated,
        "quota_available": quota_available,
        "reachable": usable,
        "usable": usable,
        "binary_found": installed,
        "key_status": "oauth" if login_present else "missing",
        "status": status,
        "detail": detail,
        "config_source": "system" if authenticated else "unavailable",
        "docs_url": "https://github.com/google/antigravity",
        "setup_hint": "Install Antigravity CLI (agy), then sign in with your Google account (`agy`)",
    }


async def _probe_linear_workspaces() -> list[dict[str, Any]]:
    """Probe configured Linear workspaces."""
    probes: list[dict[str, Any]] = []
    try:
        for workspace in await list_workspace_summaries():
            auth_status = workspace.get("auth_status") or "missing_env"
            workspace_id = workspace.get("id") or "linear"
            teams_filter = workspace.get("teams_filter") or ["*"]
            detail_parts = [
                f"Workspace: {workspace_id}",
                f"Teams: {', '.join(teams_filter) if isinstance(teams_filter, list) else '*'}",
            ]
            if workspace.get("default_repository"):
                detail_parts.append(f"Default repo: {workspace['default_repository']}")
            probes.append(
                {
                    "id": f"linear:{workspace_id}",
                    "label": "Linear" if workspace_id == "personal" else f"Linear ({workspace_id})",
                    "icon": "linear",
                    "installed": True,
                    "authenticated": auth_status == "ok",
                    "reachable": auth_status == "ok",
                    "usable": auth_status == "ok",
                    "binary_found": auth_status == "ok",
                    "status": "ready" if auth_status == "ok" else auth_status,
                    "detail": " | ".join(detail_parts),
                    "config_source": workspace.get("auth_kind") or "api_key",
                    "docs_url": "https://linear.app/settings/api",
                    "setup_hint": ("Set LINEAR_API_KEY in ~/.config/runner-dashboard/env (Linear personal API key)."),
                    "workspace_id": workspace_id,
                }
            )
    except (OSError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("Failed to enumerate Linear workspace credential probes: %s", type(exc).__name__)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        log.warning("Unexpected failure enumerating Linear workspace probes: %s", type(exc).__name__)
    return probes


async def _collect_all_probes() -> list[dict[str, Any]]:
    """Collect all provider and integration probes."""
    gh_probe, cline_probe, linear_probes = await asyncio.gather(
        _probe_github_cli(),
        _probe_cline(),
        _probe_linear_workspaces(),
    )
    probes: list[dict[str, Any]] = [
        gh_probe,
        _probe_jules_cli(),
        _probe_jules_api(),
        _probe_codex_cli(),
        _probe_claude_code_cli(),
        cline_probe,
        _probe_gemini_cli(),
        _probe_ollama(),
        _probe_antigravity_cli(),
    ]
    probes.extend(linear_probes)
    return probes


def _build_credentials_summary(probes: list[dict[str, Any]]) -> dict[str, int]:
    """Calculate summary metrics for a list of credential probes."""
    ready = sum(1 for p in probes if p.get("usable"))
    return {
        "total": len(probes),
        "ready": ready,
        "not_ready": len(probes) - ready,
    }


@router.get("/credentials")
async def get_credentials(request: Request) -> dict[str, Any]:
    """Probe provider credential and connectivity state. Never exposes secret values."""
    _require_local_request(request)
    cached = cache_get("credentials_probe", CacheTtl.CREDENTIALS_S)
    if cached is not None and isinstance(cached, dict):
        return cached

    probes = await _collect_all_probes()
    result = {
        "probes": probes,
        "summary": _build_credentials_summary(probes),
        "probed_at": datetime.now(UTC).isoformat(),
    }
    cache_set("credentials_probe", result)
    return result


@router.get("/cline/status")
async def get_cline_status(request: Request) -> dict[str, Any]:
    """Return Cline extension detection status."""
    _require_local_request(request)
    probe = await _probe_cline()
    return {
        "status": (
            "extension_installed"
            if (probe["installed"] and probe["compatible_key_set"])
            else ("extension_installed" if probe["installed"] else "not_installed")
        ),
        "vscode_found": probe["vscode_found"],
        "vscode_cli_probe_skipped": probe["vscode_cli_probe_skipped"],
        "compatible_key_set": probe["compatible_key_set"],
        "detail": (
            "Cline extension installed + compatible API key found"
            if (probe["installed"] and probe["compatible_key_set"])
            else (
                "Cline extension installed; set ANTHROPIC_API_KEY or OPENAI_API_KEY"
                if probe["installed"]
                else probe["detail"]
            )
        ),
    }


@router.get("/ollama/status")
async def get_ollama_status(request: Request) -> dict[str, Any]:
    """Return whether ollama serve is running and the base URL."""
    _require_local_request(request)
    ollama_binary = shutil.which("ollama")
    if not ollama_binary:
        raise HTTPException(status_code=503, detail="ollama not found on PATH")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        running = result.returncode == 0
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.warning("Ollama status probe failed: %s", type(exc).__name__)
        running = False

    return {
        "running": running,
        "base_url": "http://localhost:11434",
        "binary": ollama_binary,
    }


@router.get("/ollama/models")
async def get_ollama_models(request: Request) -> dict[str, Any]:
    """List installed Ollama models via `ollama list`."""
    _require_local_request(request)
    ollama_binary = shutil.which("ollama")
    if not ollama_binary:
        raise HTTPException(status_code=503, detail="ollama not found on PATH")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"ollama list failed: {result.stderr.strip()[:200]}",
            )
        models = []
        for line in result.stdout.splitlines()[1:]:  # skip header
            if not line.strip():
                continue
            parts = line.split(maxsplit=3)
            if parts:
                models.append(parts[0])
        return {"models": models}
    except HTTPException:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError, KeyError) as exc:
        log.warning("Failed to list ollama models: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"Failed to list models: {type(exc).__name__}") from exc


class LaunchAuthRequest(BaseModel):
    provider: str = Field(..., description="Provider id to launch auth for")


@router.post("/credentials/launch-auth")
async def launch_auth(body: LaunchAuthRequest, request: Request) -> dict[str, Any]:
    """Launch a provider's browser auth flow in a subprocess.

    Returns immediately with a job_id; the UI can poll /status.
    """
    _require_local_request(request)
    provider_id = body.provider.strip()

    if not provider_id:
        raise HTTPException(status_code=422, detail="provider is required")

    auth_commands: dict[str, list[str]] = {
        "gemini": ["gemini", "auth", "login"],
        "gemini_cli": ["gemini", "auth", "login"],
        "claude": ["claude", "auth", "login"],
        "claude_code_cli": ["claude", "auth", "login"],
        "anthropic": ["claude", "auth", "login"],
    }

    cmd = auth_commands.get(provider_id)
    if not cmd:
        raise HTTPException(
            status_code=422,
            detail=f"provider '{provider_id}' does not support launch-auth. Allowed: {sorted(auth_commands)}",
        )

    job_id = f"{provider_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    client_host = request.client.host if request.client else "unknown"
    log.info("launch_auth: provider=%s job_id=%s by=%s", provider_id, job_id, client_host)

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        log.warning("launch_auth binary not found for provider %s: %s", provider_id, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail=f"Binary not found for provider '{provider_id}'",
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("launch_auth failed for provider %s: %s", provider_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to launch auth subprocess") from exc

    return {"ok": True, "provider_id": provider_id, "job_id": job_id}
