# ruff: noqa: B008
import secrets
import time
from pathlib import Path

import yaml
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyCookie, APIKeyHeader
from pydantic import BaseModel


class Principal(BaseModel):
    id: str
    type: str  # 'human' or 'bot'
    name: str
    roles: list[str] = []
    github_username: str | None = None
    email: str | None = None


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

        with open(self.principals_path) as f:
            data = yaml.safe_load(f)

        if not data or "principals" not in data:
            return

        self.principals = {}
        for p in data["principals"]:
            prin = Principal(**p)
            self.principals[prin.id] = prin

    def load_tokens(self):
        if not self.tokens_path.exists():
            self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.tokens_path, "w") as f:
                yaml.dump({"tokens": []}, f)
            return

        with open(self.tokens_path) as f:
            data = yaml.safe_load(f)

        if not data or "tokens" not in data:
            return

        self.tokens = []
        for t in data["tokens"]:
            self.tokens.append(TokenRecord(**t))

    def save_tokens(self):
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tokens_path, "w") as f:
            yaml.dump({"tokens": [t.model_dump() for t in self.tokens]}, f)

    def mint_service_token(
        self, principal_id: str, name: str, expires_in_days: int | None = None
    ) -> str:
        if principal_id not in self.principals:
            raise ValueError(f"Principal {principal_id} not found")

        prin = self.principals[principal_id]
        if prin.type != "bot":
            raise ValueError("Service tokens can only be minted for bot principals")

        raw_token = "svc_" + secrets.token_urlsafe(32)
        import hashlib

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        expires_at = (
            time.time() + (expires_in_days * 86400) if expires_in_days else None
        )

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


def require_principal(
    request: Request,
    header_token: str | None = Depends(auth_header),
    cookie_token: str | None = Depends(auth_cookie),
) -> Principal:
    # 1. Check Bearer token
    if header_token and header_token.startswith("Bearer "):
        raw_token = header_token.replace("Bearer ", "")
        prin = identity_manager.verify_token(raw_token)
        if prin:
            return prin

    # 2. Check session
    if hasattr(request, "session"):
        principal_id = request.session.get("principal_id")
        if principal_id and principal_id in identity_manager.principals:
            return identity_manager.principals[principal_id]

    # Fail closed
    raise HTTPException(status_code=401, detail="Authentication required")


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
            if s == required_scope or (
                s.endswith("*") and required_scope.startswith(s[:-1])
            ):
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
