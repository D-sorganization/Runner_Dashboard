"""Cheap OAuth session validity for staff CLI providers (#2170).

Login *files* alone are insufficient: expired Google/Anthropic seats still leave
artifacts on disk. This module checks token expiry when present and otherwise runs a
short, non-interactive CLI probe (cached) using the same failure phrases staff runs
emit at runtime.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cache_utils import cache_delete, cache_get, cache_set
from security import safe_subprocess_env

log = logging.getLogger("dashboard.staff.cli_oauth_session")

_PROBE_CACHE_TTL_S = 60
_AUTH_EXPIRED_MARKERS = (
    "authentication required",
    "authentication timed out",
    "authentication expired",
    "please visit the url to log in",
    "waiting for authentication",
    "not authenticated",
    "please run claude auth login",
    "auth login",
)


def _runner_dashboard_claude_dir() -> Path:
    return Path.home() / ".config" / "runner-dashboard" / "claude"


def resolve_claude_config_dir() -> Path:
    """Config dir for the Claude seat (runner-dashboard first, then CLI default)."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    seat = _runner_dashboard_claude_dir()
    if (seat / ".credentials.json").is_file():
        return seat
    return Path.home() / ".claude"


def resolve_antigravity_home() -> Path:
    configured = os.environ.get("ANTIGRAVITY_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".gemini"


def _expiry_epoch_from_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        return ts
    if isinstance(raw, str) and raw.strip().isdigit():
        return _expiry_epoch_from_value(int(raw.strip()))
    return None


def _find_expiry_epoch(data: Any) -> float | None:
    if isinstance(data, dict):
        for key in ("expiresAt", "expires_at", "expiry", "expiration", "expirationTime"):
            if key in data:
                found = _expiry_epoch_from_value(data[key])
                if found is not None:
                    return found
        for value in data.values():
            found = _find_expiry_epoch(value)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_expiry_epoch(item)
            if found is not None:
                return found
    return None


def oauth_expired_in_file(path: Path, *, skew_seconds: float = 120.0) -> bool:
    """True when ``path`` exists and its embedded OAuth expiry is in the past."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    expiry = _find_expiry_epoch(data)
    if expiry is None:
        return False
    return expiry <= time.time() + skew_seconds


def _combined_output(proc: subprocess.CompletedProcess[str]) -> str:
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def _output_indicates_auth_expired(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in _AUTH_EXPIRED_MARKERS)


def _run_probe(cmd: list[str], env: Mapping[str, str] | None = None, timeout: float = 8.0) -> tuple[bool, str]:
    """Run a non-interactive auth probe. Post: never raises."""
    try:
        proc_env = safe_subprocess_env()
        if env:
            proc_env = {**proc_env, **dict(env)}
        proc = subprocess.run(  # noqa: S603 — argv built by trusted callers
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=proc_env,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.debug("cli oauth probe failed for %s: %s", cmd[0], type(exc).__name__)
        return False, f"{cmd[0]} auth probe failed ({type(exc).__name__})"
    combined = _combined_output(proc)
    if proc.returncode == 0 and not _output_indicates_auth_expired(combined):
        return True, "OAuth session active"
    snippet = combined.strip().splitlines()
    detail = snippet[-1][:240] if snippet else f"{cmd[0]} auth probe exit {proc.returncode}"
    return False, detail


def _cached_probe(cache_key: str, factory: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    cached = cache_get(cache_key, _PROBE_CACHE_TTL_S)
    if cached is not None and isinstance(cached, tuple) and len(cached) == 2:
        return bool(cached[0]), str(cached[1])
    result = factory()
    cache_set(cache_key, result)
    return result


def invalidate_oauth_probe_cache() -> None:
    cache_delete("cli_oauth:claude")
    cache_delete("cli_oauth:antigravity")


def claude_oauth_session_alive(binary: str | None = None) -> tuple[bool, str]:
    """Whether the Claude Code seat on this node can run without an interactive login."""
    cred = resolve_claude_config_dir() / ".credentials.json"
    if not cred.is_file():
        return False, "no Claude login file"
    if oauth_expired_in_file(cred):
        return False, "Claude OAuth token expired (credentials file)"
    exe = binary or "claude"

    def _probe() -> tuple[bool, str]:
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(resolve_claude_config_dir())}
        return _run_probe([exe, "auth", "status"], env=env)

    return _cached_probe("cli_oauth:claude", _probe)


def antigravity_oauth_session_alive(binary: str | None = None) -> tuple[bool, str]:
    """Whether the Antigravity (agy) seat on this node can run without an interactive login."""
    home = resolve_antigravity_home()
    oauth_file = home / "oauth_creds.json"
    login_markers = (
        home / "settings.json",
        home / "antigravity-cli" / "installation_id",
        home / "antigravity-cli" / "jetski_state.pbtxt",
    )
    if not any(p.is_file() for p in login_markers) and not oauth_file.is_file():
        return False, "no Antigravity login file"
    if oauth_expired_in_file(oauth_file):
        return False, "Antigravity OAuth token expired"
    exe = binary or "agy"

    def _probe() -> tuple[bool, str]:
        env = {**os.environ, "ANTIGRAVITY_HOME": str(home)}
        return _run_probe([exe, "auth", "status"], env=env)

    return _cached_probe("cli_oauth:antigravity", _probe)


_PROVIDER_CHECKERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "claude": lambda: claude_oauth_session_alive(),
    "claude_code_cli": lambda: claude_oauth_session_alive(),
    "antigravity": lambda: antigravity_oauth_session_alive(),
}


def oauth_expiry_epoch_for_provider(provider_or_credential_id: str) -> float | None:
    """Best-effort OAuth expiry epoch for preflight warnings; ``None`` when unknown."""
    if provider_or_credential_id in ("claude", "claude_code_cli"):
        cred = resolve_claude_config_dir() / ".credentials.json"
        if cred.is_file():
            try:
                data = json.loads(cred.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return _find_expiry_epoch(data)
    if provider_or_credential_id == "antigravity":
        oauth_file = resolve_antigravity_home() / "oauth_creds.json"
        if oauth_file.is_file():
            try:
                data = json.loads(oauth_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return _find_expiry_epoch(data)
    if provider_or_credential_id == "codex":
        home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        auth = home / "auth.json"
        if auth.is_file():
            try:
                data = json.loads(auth.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return _find_expiry_epoch(data)
    return None


def provider_oauth_session_alive(provider_or_credential_id: str) -> tuple[bool, str]:
    """Map dashboard provider / credential id to a live OAuth session check."""
    checker = _PROVIDER_CHECKERS.get(provider_or_credential_id)
    if checker is None:
        if provider_or_credential_id == "codex":
            if os.environ.get("STAFF_E2E_HERMETIC", "").strip() == "1":
                return True, ""
            home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
            if not (home / "auth.json").is_file():
                return False, "no Codex auth.json"
            if oauth_expired_in_file(home / "auth.json"):
                return False, "Codex OAuth token expired (auth.json)"
        return True, ""
    return checker()
