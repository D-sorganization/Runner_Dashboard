"""Fleet GitHub App token minting for node-level ``gh`` / ``git`` wrappers.

Fail-closed: never fall back to personal ``gh auth`` or stored operator tokens.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

MINT_FAILURE_EXIT = 3
MINT_FAILURE_MESSAGE = "fleet App token mint failed; refusing to fall back to personal auth"

_TOKEN_KEYS = ("GH_TOKEN", "GITHUB_TOKEN")


class FleetGhMintError(RuntimeError):
    """App installation token could not be minted."""


def scrub_personal_github_tokens(env: Mapping[str, str]) -> dict[str, str]:
    """Remove inherited operator tokens before invoking fleet ``gh``."""
    return {k: v for k, v in env.items() if k not in _TOKEN_KEYS}


def _env_file_path() -> Path:
    raw = os.environ.get("FLEET_GH_ENV_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "runner-dashboard" / "env"


def _apply_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _backend_root() -> Path | None:
    for key in ("FLEET_GH_BACKEND_ROOT", "RUNNER_DASHBOARD_BACKEND"):
        raw = os.environ.get(key, "").strip()
        if raw:
            root = Path(raw).expanduser()
            if (root / "github_app_auth.py").is_file():
                return root
    deploy = os.environ.get("RUNNER_DASHBOARD_DEPLOY_DIR", "").strip()
    if deploy:
        root = Path(deploy).expanduser() / "backend"
        if (root / "github_app_auth.py").is_file():
            return root
    return None


def _mint_installation_token() -> str:
    _apply_env_file(_env_file_path())
    backend = _backend_root()
    if backend is None:
        raise FleetGhMintError(MINT_FAILURE_MESSAGE)
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from github_app_auth import github_app_auth_configured, mint_installation_access_token  # noqa: PLC0415

    if not github_app_auth_configured():
        raise FleetGhMintError(MINT_FAILURE_MESSAGE)
    try:
        minted = mint_installation_access_token()
    except Exception:
        raise FleetGhMintError(MINT_FAILURE_MESSAGE) from None
    token = str(minted.token or "").strip()
    if not token:
        raise FleetGhMintError(MINT_FAILURE_MESSAGE)
    return token


def mint_fleet_app_token(base_env: Mapping[str, str] | None = None) -> str:
    """Mint a fleet App token using dashboard GitHub App configuration."""
    if base_env:
        for key, value in base_env.items():
            if key.startswith("GITHUB_APP_"):
                os.environ[key] = value
    try:
        return _mint_installation_token()
    except FleetGhMintError:
        raise
    except Exception:
        raise FleetGhMintError(MINT_FAILURE_MESSAGE) from None


def apply_fleet_app_token(env: dict[str, str]) -> dict[str, str]:
    """Scrub inherited tokens and set only the fleet App installation token."""
    cleaned = scrub_personal_github_tokens(env)
    token = mint_fleet_app_token(cleaned)
    cleaned["GH_TOKEN"] = token
    cleaned["GITHUB_TOKEN"] = token
    return cleaned
