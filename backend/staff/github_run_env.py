"""Per-run GitHub App installation tokens for staff subprocesses (#2038)."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from github_app_auth import (
    github_app_auth_configured,
    mint_installation_access_token,
)
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
    out: dict[str, str] = {
        "contents": access,
        "pull_requests": access,
        "issues": "write" if perms.get("open_pr") else "read",
        "metadata": "read",
        "checks": "read",
    }
    if writes_code or perms.get("open_pr"):
        out["workflows"] = "read"
    fleet_actions = role.fleet_actions
    if any("workflow" in a or "queue" in a for a in fleet_actions):
        out["actions"] = "read"
        out["workflows"] = "write"
    return out


def mint_staff_run_github_token(role: RoleSpec, repo: str) -> StaffGithubToken:
    """Mint or resolve the GitHub credential for one staff run.

    Post: never returns an empty token. Raises ``RuntimeError`` when App auth is
    unavailable and operator fallback is not explicitly enabled.
    """
    if os.environ.get("STAFF_E2E_HERMETIC", "").strip() == "1":
        token = "ghs_" + "E2E" * 12 + "_hermetic"
        return StaffGithubToken(token=token, expires_at=0.0, source="e2e_hermetic")

    if github_app_auth_configured():
        repositories = [repo] if repo else None
        try:
            minted = mint_installation_access_token(
                repositories=repositories,
                permissions=github_permissions_for_role(role),
            )
        except Exception as exc:
            if operator_token_fallback_enabled():
                op = _operator_token_from_environ()
                if op:
                    return StaffGithubToken(token=op, expires_at=0.0, source="operator")
            raise RuntimeError(f"GitHub App installation token minting failed: {exc}") from exc
        return StaffGithubToken(token=minted.token, expires_at=minted.expires_at, source="github_app")

    if operator_token_fallback_enabled():
        op = _operator_token_from_environ()
        if op:
            return StaffGithubToken(token=op, expires_at=0.0, source="operator")
    raise RuntimeError(
        "GitHub App is not configured for staff runs; set GITHUB_APP_* on the dashboard "
        "node or opt in with STAFF_ALLOW_OPERATOR_TOKEN=1 (operator PAT fallback only)."
    )


def strip_github_secrets_from_child_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k not in _CHILD_STRIP_KEYS}


def _credential_url(run_id: str, dashboard_port: str | None) -> str:
    port = (dashboard_port or os.environ.get("DASHBOARD_PORT", "8321")).strip()
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
    env["STAFF_REAL_GH_PATH"] = real_gh_path
    helper = f"!{py} -m staff.github_git_credential"
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "credential.https://github.com.helper"
    env["GIT_CONFIG_VALUE_0"] = helper
    env["GIT_CONFIG_KEY_1"] = "credential.useHttpPath"
    env["GIT_CONFIG_VALUE_1"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    wrapper_dir = str(_WRAPPERS_DIR)
    env["PATH"] = os.pathsep.join([wrapper_dir, env.get("PATH", "")])
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
    cred = mint_staff_run_github_token(role, repo)
    env = build_staff_run_env(
        base_env,
        cred.token,
        run_id=run_id,
        real_gh_path=real_gh_path,
        python_executable=python_executable,
        dashboard_port=dashboard_port,
    )
    return env, cred
