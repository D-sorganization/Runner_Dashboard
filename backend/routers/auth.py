# ruff: noqa: B008
import os
import secrets
from typing import Any

import httpx
import session_management as sm
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from identity import Principal, identity_manager, require_principal
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")


@router.get("/github")
async def github_login(request: Request):
    """Start GitHub OAuth flow."""
    if not GITHUB_CLIENT_ID:
        # Fallback to dev login if no client ID is configured
        return RedirectResponse(url="/api/auth/dev-login")

    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    redirect_uri = (
        f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&state={state}&scope=read:user"
    )
    return RedirectResponse(url=redirect_uri)


@router.get("/callback")
async def github_callback(request: Request, code: str, state: str):
    """Handle GitHub OAuth callback."""
    saved_state = request.session.get("oauth_state")
    if not saved_state or state != saved_state:
        raise HTTPException(status_code=400, detail="Invalid state")

    async with httpx.AsyncClient() as client:
        # Exchange code for token
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to get access token")

        # Get user info
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        user_data = user_resp.json()
        github_login = user_data.get("login")

    # Find principal by github username
    matched_principal = None
    for p in identity_manager.principals.values():
        if p.github_username and p.github_username.lower() == github_login.lower():
            matched_principal = p
            break

    if not matched_principal:
        # Deny access if not in principals.yml
        raise HTTPException(status_code=403, detail="User not authorized")

    request.session["principal_id"] = matched_principal.id
    request.session["session_id"] = sm.register_session(
        matched_principal.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse(url="/")


@router.get("/dev-login")
async def dev_login(request: Request):
    """Development login when OAuth is not configured."""
    if GITHUB_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Dev login disabled when OAuth is configured")

    # Just grab the first human principal
    for p in identity_manager.principals.values():
        if p.type == "human":
            request.session["principal_id"] = p.id
            request.session["session_id"] = sm.register_session(
                p.id,
                user_agent=request.headers.get("user-agent"),
                ip_address=request.client.host if request.client else None,
            )
            return RedirectResponse(url="/")

    # If none exists, create a dummy one
    dummy = Principal(id="dev-user", type="human", name="Developer")
    identity_manager.principals["dev-user"] = dummy
    request.session["principal_id"] = "dev-user"
    request.session["session_id"] = sm.register_session(
        "dev-user",
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return RedirectResponse(url="/")


@router.post("/logout")
async def logout(request: Request):
    """Logout current user."""
    principal_id = request.session.get("principal_id")
    session_id = request.session.get("session_id")
    request.session.clear()
    if principal_id:
        if session_id:
            sm.revoke_session(session_id)
        else:
            # Fallback: revoke all sessions for this principal
            sm.revoke_all_sessions_for_principal(principal_id)
    return {"status": "logged_out"}


@router.get("/me")
async def get_me(principal: Principal = Depends(require_principal)):  # noqa: B008
    """Get current user info."""
    return principal


@router.get("/sessions")
async def list_sessions(principal: Principal = Depends(require_principal)):  # noqa: B008
    """List active sessions for the current user."""
    sessions = sm.list_sessions_for_principal(principal.id)
    return {
        "sessions": [
            {
                "session_hash": sm.hash_session_id(s["session_id"]),
                "created_at": s["created_at"],
                "last_seen_at": s["last_seen_at"],
                "user_agent": s["user_agent"],
                "ip_address": s["ip_address"],
            }
            for s in sessions
        ]
    }


@router.delete("/sessions/{session_id_hash}")
async def revoke_session_by_hash(
    session_id_hash: str,
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    """Revoke a specific session by its hash (remote logout)."""
    sessions = sm.list_sessions_for_principal(principal.id)
    target_session_id = None
    for s in sessions:
        if sm.hash_session_id(s["session_id"]) == session_id_hash:
            target_session_id = s["session_id"]
            break

    if not target_session_id:
        raise HTTPException(status_code=404, detail="Session not found")

    revoked = sm.revoke_session(target_session_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Session already revoked or not found")
    return {"status": "revoked", "session_hash": session_id_hash}


class RemoteLogoutRequest(BaseModel):
    exclude_current: bool = True


@router.post("/logout/all")
async def logout_all(
    request: Request,
    body: RemoteLogoutRequest = RemoteLogoutRequest(),
    principal: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    """Remote logout: revoke all sessions for this principal."""
    current_session_id = request.session.get("session_id")
    exclude = current_session_id if body.exclude_current else None
    revoked_count = sm.revoke_all_sessions_for_principal(principal.id, exclude_session_id=exclude)
    request.session.clear()
    return {"status": "logged_out", "revoked_sessions": revoked_count}


class MintTokenRequest(BaseModel):
    principal_id: str
    name: str
    expires_in_days: int = 30


@router.post("/tokens")
async def mint_token(req: MintTokenRequest, current_user: Principal = Depends(require_principal)):  # noqa: B008
    """Mint a new service token (requires admin role or similar)."""
    if "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Admin role required to mint tokens")

    try:
        raw_token = identity_manager.mint_service_token(req.principal_id, req.name, req.expires_in_days)
        return {"token": raw_token}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/tokens/{token_hash}")
async def revoke_token(token_hash: str, current_user: Principal = Depends(require_principal)):  # noqa: B008
    """Revoke a token."""
    if "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Admin role required to revoke tokens")
    identity_manager.revoke_token(token_hash)
    return {"status": "revoked"}
