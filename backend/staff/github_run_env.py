"""Per-run GitHub App installation tokens for staff subprocesses (#2038)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from github_app_auth import (
    github_app_auth_configured,
    mint_installation_access_token,
)
from staff.roles import RoleSpec

# GitHub installation tokens last at most one hour; stay below that for long runs.
GITHUB_INSTALLATION_TOKEN_MAX_RUN_SECONDS = int(os.environ.get("STAFF_GITHUB_TOKEN_MAX_RUN_SECONDS", "3300"))

_CHILD_STRIP_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_FILE",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
)


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


def cap_run_timeout_for_github_token(seconds: float) -> float:
    return min(float(seconds), float(GITHUB_INSTALLATION_TOKEN_MAX_RUN_SECONDS))


def strip_github_secrets_from_child_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k not in _CHILD_STRIP_KEYS}


def build_staff_run_env(base_env: Mapping[str, str], github_token: str) -> dict[str, str]:
    """Child-process env: operator/App secrets stripped, run token and git auth injected."""
    env = strip_github_secrets_from_child_env(base_env)
    env = dict(env)
    env["GH_TOKEN"] = github_token
    env["GITHUB_TOKEN"] = github_token
    # In-memory git URL rewrite; never writes credentials into the worktree.
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "url.https://github.com/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = f"https://x-access-token:{github_token}@github.com/"
    return env


def prepare_staff_run_github_env(
    base_env: Mapping[str, str],
    role: RoleSpec,
    repo: str,
) -> tuple[dict[str, str], StaffGithubToken]:
    cred = mint_staff_run_github_token(role, repo)
    return build_staff_run_env(base_env, cred.token), cred
