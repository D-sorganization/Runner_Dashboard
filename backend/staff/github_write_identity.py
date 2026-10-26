"""GitHub token identity checks for staff fleet writes (#3000)."""

from __future__ import annotations

from collections.abc import Mapping

APP_TOKEN_PREFIXES = ("ghs_",)
USER_TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_")

STAFF_GITHUB_WRITE_REFUSED = (
    "GitHub write requires a fleet GitHub App installation token (ghs_); "
    "personal or missing credentials are refused for staff runs"
)


def classify_github_token(token: str) -> str:
    """Return ``app``, ``user``, ``unknown``, or ``none`` without logging the token."""
    value = (token or "").strip()
    if not value:
        return "none"
    if value.startswith(APP_TOKEN_PREFIXES):
        return "app"
    if value.startswith(USER_TOKEN_PREFIXES):
        return "user"
    return "unknown"


def active_github_token_kind(env: Mapping[str, str]) -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        kind = classify_github_token(str(env.get(key, "")))
        if kind != "none":
            return kind
    return "none"


def require_github_app_installation_token(env: Mapping[str, str]) -> None:
    """Pre: env is the subprocess environment about to invoke gh. Post: raises when not App."""
    if env.get("FLEET_GH_REQUIRE_APP", "").strip() != "1":
        return
    kind = active_github_token_kind(env)
    if kind != "app":
        raise RuntimeError(STAFF_GITHUB_WRITE_REFUSED)
