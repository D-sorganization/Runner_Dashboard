# ruff: noqa: B008
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

import yaml
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyCookie, APIKeyHeader
from pydantic import BaseModel, Field
from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard")


class Quota(BaseModel):
    """Per-principal resource quotas."""

    max_runners: int = Field(default=2, ge=0)
    agent_spend_usd_day: float = Field(default=10.0, ge=0.0)
    local_app_slots: int = Field(default=1, ge=0)


class Principal(BaseModel):
    id: str
    type: str  # 'human' or 'bot'
    name: str
    roles: list[str] = []
    github_username: str | None = None
    email: str | None = None
    quotas: Quota = Field(default_factory=Quota)


class TokenRecord(BaseModel):
    token_hash: str
    principal_id: str
    created_at: float
    expires_at: float | None = None
    name: str


class IdentityManager:
    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = config_dir
        self.principals_path = self.config_dir / "principals.yml"
        self.tokens_path = self.config_dir / "tokens.yml"
        self.principals: dict[str, Principal] = {}
        self.tokens: list[TokenRecord] = []
        self.load_principals()
        self.load_tokens()

    def load_principals(self):
        if not self.principals_path.exists():
            # Create default empty config
            self.principals_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.principals_path, "w") as f:
                yaml.dump({"principals": []}, f)
            return

        # Security validation for issue #355: validate path before loading
        validate_config_path(self.principals_path)

        # Use safe_yaml_load which validates path security
        data = safe_yaml_load(self.principals_path)

        if not data or "principals" not in data:
            return

        self.principals = {}
        for p in data["principals"]:
            prin = Principal(**p)
            self.principals[prin.id] = prin

    def save_principals(self) -> None:
        """Atomically persist the in-memory principals dict to ``principals.yml``.

        Uses a temp-file + os.replace pattern so readers never see a partial write.

        Security (issue #355): Validates that the config directory is within allowed
        roots before writing.
        """
        self.principals_path.parent.mkdir(parents=True, exist_ok=True)

        # Security validation: ensure config dir is within allowed roots
        validate_config_path(self.principals_path.parent)

        payload = {"principals": [p.model_dump() for p in self.principals.values()]}
        fd, tmp_path = tempfile.mkstemp(
            dir=self.principals_path.parent,
            prefix=".tmp-principals-",
            suffix=".yml",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.dump(payload, fh, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, self.principals_path)
        except (OSError, yaml.YAMLError):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_principal(self, principal_id: str) -> Principal | None:
        return self.principals.get(principal_id)

    def load_tokens(self):
        if not self.tokens_path.exists():
            self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.tokens_path, "w") as f:
                yaml.dump({"tokens": []}, f)
            return

        # Security validation for issue #355: validate path before loading
        validate_config_path(self.tokens_path)

        # Use safe_yaml_load which validates path security
        data = safe_yaml_load(self.tokens_path)

        if not data or "tokens" not in data:
            return

        self.tokens = []
        for t in data["tokens"]:
            self.tokens.append(TokenRecord(**t))

    def save_tokens(self):
        """Save tokens with security validation (issue #355)."""
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True)

        # Security validation: ensure config dir is within allowed roots
        validate_config_path(self.tokens_path.parent)

        with open(self.tokens_path, "w") as f:
            yaml.dump({"tokens": [t.model_dump() for t in self.tokens]}, f)

    def mint_service_token(self, principal_id: str, name: str, expires_in_days: int | None = None) -> str:
        if principal_id not in self.principals:
            raise ValueError(f"Principal {principal_id} not found")

        prin = self.principals[principal_id]
        if prin.type != "bot":
            raise ValueError("Service tokens can only be minted for bot principals")

        raw_token = "svc_" + secrets.token_urlsafe(32)
        import hashlib

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        expires_at = time.time() + (expires_in_days * 86400) if expires_in_days else None

        record = TokenRecord(
            token_hash=token_hash,
            principal_id=principal_id,
            created_at=time.time(),
            expires_at=expires_at,
            name=name,
        )
        self.tokens.append(record)
        self.save_tokens()
        return raw_token

    def revoke_token(self, token_hash: str):
        self.tokens = [t for t in self.tokens if t.token_hash != token_hash]
        self.save_tokens()

    def verify_token(self, raw_token: str) -> Principal | None:
        import hashlib

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        for t in self.tokens:
            if t.token_hash == token_hash:
                if t.expires_at and time.time() > t.expires_at:
                    return None
                return self.principals.get(t.principal_id)
        return None


identity_manager = IdentityManager()

auth_header = APIKeyHeader(name="Authorization", auto_error=False)
auth_cookie = APIKeyCookie(name="dashboard_session", auto_error=False)

# Synthetic admin principal granted automatically to loopback (127.0.0.1) requests.
# This allows the local browser to use all authenticated endpoints without manual
# principal setup on a single-machine deployment.
_LOOPBACK_ADMIN = Principal(
    id="__loopback__",
    type="human",
    name="Local Admin (loopback)",
    roles=["admin"],
)


def require_principal(
    request: Request,
    header_token: str | None = Depends(auth_header),
    cookie_token: str | None = Depends(auth_cookie),
) -> Principal:
    prin = None
    # 1. Check Bearer token
    if header_token and header_token.startswith("Bearer "):
        raw_token = header_token.replace("Bearer ", "")
        prin = identity_manager.verify_token(raw_token)

    # 2. Check session
    if not prin and hasattr(request, "session"):
        principal_id = request.session.get("principal_id")
        if principal_id and principal_id in identity_manager.principals:
            prin = identity_manager.principals[principal_id]

    # 3. Loopback bypass — requests from 127.0.0.1 or ::1 are automatically
    #    granted admin access.  This is appropriate for the local single-user
    #    dashboard that is not exposed beyond the loopback interface.
    if not prin:
        client_host = getattr(request.client, "host", "") if request.client else ""
        if client_host in ("127.0.0.1", "::1"):
            prin = _LOOPBACK_ADMIN

    if not prin:
        # Fail closed for non-loopback requests with no credentials
        raise HTTPException(status_code=401, detail="Authentication required")

    # Set default on_behalf_of
    if hasattr(request.state, "on_behalf_of"):
        pass  # already set?
    else:
        request.state.on_behalf_of = None

    # Check impersonation
    impersonate_target = request.headers.get("X-Impersonate-Principal")
    if impersonate_target:
        # Only admins can impersonate
        if "admin" in prin.roles:
            target_prin = identity_manager.principals.get(impersonate_target)
            if target_prin:
                # The returned principal is the target. The real user is on_behalf_of.
                request.state.on_behalf_of = prin.id
                return target_prin
            else:
                raise HTTPException(status_code=400, detail="Impersonation target not found")
        else:
            raise HTTPException(status_code=403, detail="Only admins can impersonate")

    return prin


SCOPE_PRESETS = {
    "admin": ["*"],
    "operator": [
        "workflows.dispatch",
        "workflows.control",
        "runners.control",
        "fleet.control",
        "remediation.dispatch",
        "heavy-tests.dispatch",
        "tests.rerun",
        "github.dispatch",
        "assistant.chat",
        "assistant.execute",
        "maxwell.control",
        "assessments.dispatch",
        "feature-requests.manage",
        "system.control",
    ],
    "viewer": ["assistant.chat"],
    "bot": [
        "remediation.dispatch",
        "workflows.dispatch",
        "heavy-tests.dispatch",
    ],
}


def require_scope(required_scope: str):
    def checker(
        principal: Principal = Depends(require_principal),
    ) -> Principal:  # noqa: B008
        principal_scopes = set()
        for role in principal.roles:
            if role in SCOPE_PRESETS:
                principal_scopes.update(SCOPE_PRESETS[role])

        if "*" in principal_scopes:
            return principal

        for s in principal_scopes:
            if s == required_scope or (s.endswith("*") and required_scope.startswith(s[:-1])):
                return principal

        raise HTTPException(
            status_code=403,
            detail={
                "error": "Authorization failed",
                "required_scope": required_scope,
                "principal": principal.id,
            },
        )

    return checker
