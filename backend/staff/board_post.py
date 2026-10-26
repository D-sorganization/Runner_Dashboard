"""Prepare GitHub env for staff board comments, claims, and presence (#3000)."""

from __future__ import annotations

from collections.abc import Mapping

from staff.github_write_identity import (
    STAFF_GITHUB_WRITE_REFUSED,
    require_github_app_installation_token,
)


def staff_github_write_env_flags() -> dict[str, str]:
    return {
        "FLEET_GH_REQUIRE_APP": "1",
        "FLEET_AGENT_SESSION": "1",
    }


def ensure_staff_github_app_write_env(base: Mapping[str, str]) -> dict[str, str]:
    """Merge staff write flags and fail closed unless GH_TOKEN/GITHUB_TOKEN is a ghs_ App token."""
    env = dict(base)
    env.update(staff_github_write_env_flags())
    require_github_app_installation_token(env)
    return env
