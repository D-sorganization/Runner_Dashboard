"""Per-run GitHub App installation tokens for staff subprocesses (#2038)."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dashboard_config import HUB_URL, MACHINE_ROLE, ORG
from github_app_auth import (
    github_app_auth_configured,
    mint_installation_access_token,
)
from security import validate_repo_slug
from staff.git_identity import staff_git_identity_env
from staff.roles import RoleSpec

_CHILD_STRIP_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_FILE",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
)

_WRAPPERS_DIR = Path(__file__).resolve().parent / "wrappers"
_BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class StaffGithubToken:
    token: str
    expires_at: float
    source: str


def operator_token_fallback_enabled() -> bool:
    return os.environ.get("STAFF_ALLOW_OPERATOR_TOKEN", "").strip() == "1"


def _operator_token_from_environ() -> str:
    return os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()


def github_permissions_for_role(role: RoleSpec) -> dict[str, str]:
    """Minimal GitHub App permissions for a staff role (fail-safe read defaults)."""
    if role.code_read_only:
        return {
            "contents": "read",
            "pull_requests": "read",
            "issues": "read",
            "metadata": "read",
            "checks": "read",
        }

    perms = role.permissions
    writes_code = bool(perms.get("push_branch") or perms.get("open_pr") or perms.get("merge"))
    access = "write" if writes_code else "read"
    return {
        "contents": access,
        "pull_requests": access,
        "issues": "write" if perms.get("open_pr") else "read",
        "metadata": "read",
        "checks": "read",
    }


def _staff_github_mint_uses_hub() -> bool:
    """True when this node must request GitHub tokens from the fleet hub (#2051)."""
    if os.environ.get("STAFF_E2E_HERMETIC", "").strip() == "1":
        return False
    role_raw = MACHINE_ROLE if MACHINE_ROLE is not None else os.environ.get("MACHINE_ROLE") or "node"
    role = role_raw.strip().lower()
    hub_raw = HUB_URL if HUB_URL is not None else os.environ.get("HUB_URL") or ""
    hub = hub_raw.strip()
    return role == "node" and bool(hub)


def mint_staff_run_github_token_local(role: RoleSpec, repo: str) -> StaffGithubToken:
    """Mint on the hub using local GitHub App credentials (#2038, #2051)."""
    if github_app_auth_configured():
        repositories = [_installation_repository_name(repo)] if repo else None
        try:
            minted = mint_installation_access_token(
                repositories=repositories,
                permissions=github_permissions_for_role(role),
            )
        except Exception as exc:
            if operator_token_fallback_enabled() and not _staff_github_mint_uses_hub():
                op = _operator_token_from_environ()
                if op:
                    return StaffGithubToken(token=op, expires_at=0.0, source="operator")
            raise RuntimeError(f"GitHub App installation token minting failed: {exc}") from exc
        return StaffGithubToken(token=minted.token, expires_at=minted.expires_at, source="github_app")

    if operator_token_fallback_enabled() and not _staff_github_mint_uses_hub():
        op = _operator_token_from_environ()
        if op:
            return StaffGithubToken(token=op, expires_at=0.0, source="operator")
    raise RuntimeError(
        "GitHub App is not configured for staff runs; set GITHUB_APP_* on the hub "
        "dashboard node or opt in with STAFF_ALLOW_OPERATOR_TOKEN=1 on the hub only."
    )


def _installation_repository_name(repo: str) -> str:
    """Validate an org/repo scope and return the repository name GitHub expects."""
    raw = str(repo or "").strip()
    if not raw:
        raise ValueError("repository name is required for installation token scope")
    owner, separator, name = raw.partition("/")
    if separator and owner != ORG:
        raise ValueError("repository outside configured organization scope")
    return validate_repo_slug(name if separator else raw)


def mint_staff_run_github_token(role: RoleSpec, repo: str, *, run_id: str = "") -> StaffGithubToken:
    """Mint or resolve the GitHub credential for one staff run.

    Post: never returns an empty token. Raises ``RuntimeError`` when App auth is
    unavailable and operator fallback is not explicitly enabled.
    """
    if os.environ.get("STAFF_E2E_HERMETIC", "").strip() == "1":
        token = "ghs_" + "E2E" * 12 + "_hermetic"
        return StaffGithubToken(token=token, expires_at=0.0, source="e2e_hermetic")

    if _staff_github_mint_uses_hub():
        from staff.fleet_run_token_client import mint_staff_run_github_token_via_hub  # noqa: PLC0415

        return mint_staff_run_github_token_via_hub(role.name, repo, run_id)

    return mint_staff_run_github_token_local(role, repo)


def strip_github_secrets_from_child_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k not in _CHILD_STRIP_KEYS}


def _credential_url(run_id: str, dashboard_port: str | None) -> str:
    port = dashboard_port if dashboard_port is not None else os.environ.get("DASHBOARD_PORT", "8321")
    port = str(port or "8321").strip()
    return f"http://127.0.0.1:{port}/api/staff/runs/{run_id}/github-credential"


def build_staff_run_env(
    base_env: Mapping[str, str],
    github_token: str,
    *,
    run_id: str,
    real_gh_path: str,
    python_executable: str | None = None,
    dashboard_port: str | None = None,
) -> dict[str, str]:
    """Child-process env: stripped secrets, bootstrap token, refresh hooks for git/gh."""
    env = strip_github_secrets_from_child_env(base_env)
    env = dict(env)
    py = python_executable or sys.executable
    env["GH_TOKEN"] = github_token
    env["GITHUB_TOKEN"] = github_token
    env["STAFF_GITHUB_CREDENTIAL_URL"] = _credential_url(run_id, dashboard_port)
    env["STAFF_REAL_GH"] = real_gh_path
    env["STAFF_REAL_GH_PATH"] = real_gh_path
    helper = f"!{py} -m staff.github_git_credential"
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "credential.https://github.com.helper"
    env["GIT_CONFIG_VALUE_0"] = helper
    env["GIT_CONFIG_KEY_1"] = "credential.useHttpPath"
    env["GIT_CONFIG_VALUE_1"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(staff_git_identity_env())
    wrapper_dir = str(_WRAPPERS_DIR)
    env["PATH"] = os.pathsep.join([wrapper_dir, env.get("PATH", "")])
    # The gh wrapper and credential helper import ``staff.*``; a target repo's cwd does not provide it (#2481).
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_BACKEND_DIR), env.get("PYTHONPATH", "")) if p)
    return env


def prepare_staff_run_github_env(
    base_env: Mapping[str, str],
    role: RoleSpec,
    repo: str,
    *,
    run_id: str,
    real_gh_path: str,
    python_executable: str | None = None,
    dashboard_port: str | None = None,
) -> tuple[dict[str, str], StaffGithubToken]:
    cred = mint_staff_run_github_token(role, repo, run_id=run_id)
    env = build_staff_run_env(
        base_env,
        cred.token,
        run_id=run_id,
        real_gh_path=real_gh_path,
        python_executable=python_executable,
        dashboard_port=dashboard_port,
    )
    return env, cred
