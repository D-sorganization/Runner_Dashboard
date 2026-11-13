"""Barb lane GitHub App installation token endpoint (#3657)."""

from __future__ import annotations

import logging
import time

import dashboard_config
from fastapi import APIRouter, Depends, HTTPException, Request
from github_app_auth import github_app_auth_configured, mint_installation_access_token
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from staff.audit import record_audit
from staff.fleet_run_token_service import full_repo_slug
from staff.github_run_env import StaffGithubToken
from staff.rate_limit import check_rate_limit, get_rate_limiter

log = logging.getLogger("dashboard.github_app_token")
router = APIRouter(tags=["github"])
_MAX_TTL = 3600.0


class GithubAppTokenRequest(BaseModel):
    repository: str | None = Field(default=None, max_length=120)


class GithubAppTokenResponse(BaseModel):
    token: str
    expires_at: float


def mint_barb_github_app_token(*, repository: str | None = None) -> StaffGithubToken:
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="GitHub App token minting is only available on the fleet hub")
    if not github_app_auth_configured():
        raise HTTPException(status_code=503, detail="GitHub App is not configured on the hub")
    repos: list[str] | None = None
    if repository and str(repository).strip():
        _owner, _sep, name = full_repo_slug(str(repository)).partition("/")
        repos = [name]
    try:
        minted = mint_installation_access_token(repositories=repos)
    except Exception as exc:
        log.error("Barb GitHub App token mint failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="GitHub App installation token minting failed") from exc
    if not minted.token:
        raise HTTPException(status_code=502, detail="GitHub App installation token minting returned empty token")
    now = time.time()
    return StaffGithubToken(
        token=minted.token,
        expires_at=min(minted.expires_at, now + _MAX_TTL),
        source="github_app",
    )


@router.post("/api/github/app-token", response_model=GithubAppTokenResponse, response_model_exclude_none=True)
async def post_github_app_token(
    request: Request,
    body: GithubAppTokenRequest | None = None,
    caller: Principal = Depends(require_scope("github.app_token")),  # noqa: B008
) -> GithubAppTokenResponse:
    check_rate_limit("github_app_token", caller, limiter=get_rate_limiter())
    repo = ((body.repository if body else None) or "").strip() or None
    cred = mint_barb_github_app_token(repository=repo)
    record_audit(
        action="github_app_token",
        target=repo or "D-sorganization/*",
        principal=caller.id,
        surface="api",
        request_id=getattr(request.state, "request_id", ""),
        detail={"repository": repo or ""},
    )
    return GithubAppTokenResponse(token=cred.token, expires_at=cred.expires_at)
