# ruff: noqa: B008
import functools
import hmac
import ipaddress
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

import session_management as sm
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
    scopes: list[str] = []
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
        self._ephemeral_principals: dict[str, Principal] = {}
        self._ephemeral_tokens: dict[str, TokenRecord] = {}
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
        validate_config_path(self.principals_path, allowed_roots=[self.config_dir.resolve()])

        # Use safe_yaml_load which validates path security
        data = safe_yaml_load(self.principals_path, allowed_roots=[self.config_dir.resolve()])

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
        validate_config_path(self.principals_path.parent, allowed_roots=[self.config_dir.resolve()])

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
        return self.principals.get(principal_id) or self._ephemeral_principals.get(principal_id)

    def load_tokens(self):
        if not self.tokens_path.exists():
            self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.tokens_path, "w") as f:
                yaml.dump({"tokens": []}, f)
            return

        # Security validation for issue #355: validate path before loading
        validate_config_path(self.tokens_path, allowed_roots=[self.config_dir.resolve()])

        # Use safe_yaml_load which validates path security
        data = safe_yaml_load(self.tokens_path, allowed_roots=[self.config_dir.resolve()])

        if not data or "tokens" not in data:
            return

        self.tokens = []
        for t in data["tokens"]:
            self.tokens.append(TokenRecord(**t))

    def save_tokens(self):
        """Atomically persist tokens to ``tokens.yml`` (security: issue #355).

        Crash-safety (issue #939a): writes via tempfile + ``os.replace`` so a
        crash mid-dump can never leave a truncated tokens.yml — which
        ``load_tokens`` would silently reset, locking every principal out.
        Mirrors :meth:`save_principals`.
        """
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True)

        # Security validation: ensure config dir is within allowed roots
        validate_config_path(self.tokens_path.parent, allowed_roots=[self.config_dir.resolve()])

        payload = {"tokens": [t.model_dump() for t in self.tokens]}
        fd, tmp_path = tempfile.mkstemp(
            dir=self.tokens_path.parent,
            prefix=".tmp-tokens-",
            suffix=".yml",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.dump(payload, fh, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, self.tokens_path)
        except (OSError, yaml.YAMLError):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

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

        # Ephemeral run tokens (issue #1310, SC-E2)
        e_rec = self._ephemeral_tokens.get(token_hash)
        if e_rec is not None:
            if e_rec.expires_at and time.time() > e_rec.expires_at:
                return None
            return self._ephemeral_principals.get(e_rec.principal_id)

        for t in self.tokens:
            if t.token_hash == token_hash:
                if t.expires_at and time.time() > t.expires_at:
                    return None
                return self.principals.get(t.principal_id)
        return None

    def add_ephemeral_token(
        self,
        token_hash: str,
        principal: Principal,
        expires_at: float | None = None,
        name: str = "",
    ) -> None:
        """Register an in-memory ephemeral token bound to an ephemeral principal (#1310)."""
        self._ephemeral_principals[principal.id] = principal
        self._ephemeral_tokens[token_hash] = TokenRecord(
            token_hash=token_hash,
            principal_id=principal.id,
            created_at=time.time(),
            expires_at=expires_at,
            name=name,
        )

    def revoke_ephemeral_principal(self, principal_id: str) -> None:
        """Revoke any in-memory ephemeral principal and associated tokens (#1310)."""
        self._ephemeral_principals.pop(principal_id, None)
        self._ephemeral_tokens = {th: t for th, t in self._ephemeral_tokens.items() if t.principal_id != principal_id}


def resolve_identity_dir() -> Path:
    """Return the directory holding principals.yml / tokens.yml (issue #944).

    Anchored to a stable, CWD-independent location so launching the server from
    any working directory uses the *same* identity store (the old
    ``Path("config")`` default silently created a fresh empty store — and lost
    all principals/tokens — when started from another CWD).

    Resolution order:
    1. ``DASHBOARD_IDENTITY_DIR`` env override (explicit operator choice).
    2. ``XDG_CONFIG_HOME/runner-dashboard`` (or ``~/.config/runner-dashboard``)
       — the same convention as ``_load_or_generate_api_key`` (server.py) and
       ``auth_webauthn``.

    Post-condition: returns an absolute ``Path``.
    """
    override = os.environ.get("DASHBOARD_IDENTITY_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (base / "runner-dashboard").expanduser().resolve()


_IDENTITY_DIR = resolve_identity_dir()
log.info("Identity store directory: %s", _IDENTITY_DIR)
identity_manager = IdentityManager(config_dir=_IDENTITY_DIR)

auth_header = APIKeyHeader(name="Authorization", auto_error=False)
auth_cookie = APIKeyCookie(name="dashboard_session", auto_error=False)


def _loopback_auth_enabled() -> bool:
    return os.environ.get("DASHBOARD_LOOPBACK_AUTH") == "1"


def _is_loopback_request(request: Request) -> bool:
    if not request.client:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _loopback_principal() -> Principal:
    return Principal(
        id="__loopback__",
        type="human",
        name="Loopback development admin",
        roles=["loopback"],
    )


def _optional_session(request: Request) -> dict:
    """Read session state without triggering Starlette's missing-middleware assertion."""
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        session = scope.get("session")
    else:
        # Unit-level dependency tests use a request-shaped mock rather than an
        # ASGI Request. Preserve that supported test seam without probing the
        # assertion-raising Request.session property in production.
        session = getattr(request, "session", None)
    return session if isinstance(session, dict) else {}


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

    # 2. Check session — also consult revocation store (issue #346)
    session = _optional_session(request)
    if not prin and session:
        principal_id = session.get("principal_id")
        session_id = session.get("session_id")
        if principal_id and principal_id in identity_manager.principals:
            # Reject sessions that have been revoked or FIFO-evicted
            if session_id and not sm.touch_session(session_id):
                raise HTTPException(status_code=401, detail="Session revoked or expired")
            prin = identity_manager.principals[principal_id]

    # 3. Optional local development bypass. Disabled by default and only grants
    # access to the transport peer address, never forwarded headers.
    if not prin and _loopback_auth_enabled() and _is_loopback_request(request):
        prin = _loopback_principal()

    if not prin:
        # Fail closed — all callers must present valid credentials (issue #315)
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
                client_ip = getattr(request.client, "host", "unknown") if request.client else "unknown"
                log.info(
                    "audit: impersonate admin=%s target=%s path=%s source_ip=%s",
                    prin.id,
                    target_prin.id,
                    request.url.path,
                    client_ip,
                )
                return target_prin
            else:
                raise HTTPException(status_code=400, detail="Impersonation target not found")
        else:
            raise HTTPException(status_code=403, detail="Only admins can impersonate")

    return prin


FLEET_PEER_SCOPES = frozenset({"staff.read", "staff.dispatch", "staff.cancel", "staff.chat", "fleet.maintain"})
LOOPBACK_SCOPES = frozenset(
    (
        "staff.read staff.dispatch staff.cancel staff.chat staff.holds.write "
        "workflows.dispatch workflows.control runners.control fleet.control fleet.maintain "
        "remediation.dispatch heavy-tests.dispatch tests.rerun coordination.write priorities.write proposals.write"
    ).split()
)

SCOPE_PRESETS = {
    "admin": ["*"],
    # Whitespace-separated to keep this module under the 500-line cap.
    "operator": (
        "workflows.dispatch workflows.control runners.control fleet.control remediation.dispatch "
        "heavy-tests.dispatch tests.rerun github.dispatch assistant.chat assistant.execute maxwell.control "
        "assessments.dispatch code-requests.manage feature-requests.manage system.control "
        "coordination.write priorities.write proposals.write "
        "staff.read staff.chat staff.dispatch staff.cancel staff.holds.write staff.approve staff.admin fleet.maintain"
    ).split(),
    "viewer": ["assistant.chat", "staff.read"],
    # Barb (Grok Bot), Claude Cowork, Codex orchestrate staff runs and cancel stale runs, but cannot write holds/admin.
    "bot": (
        "remediation.dispatch workflows.dispatch heavy-tests.dispatch coordination.write proposals.write "
        "staff.read staff.chat staff.dispatch staff.cancel"
    ).split(),
    "fleet-peer": sorted(FLEET_PEER_SCOPES),
    "loopback": sorted(LOOPBACK_SCOPES),
}

SCOPE_ALIASES: dict[str, str] = {
    "feature-requests.manage": "code-requests.manage",
    "code-requests.manage": "feature-requests.manage",
}


def principal_has_scope(principal: Principal, required_scope: str) -> bool:
    """True when any of the principal's role presets grants ``required_scope`` (``*`` and ``x.*`` wildcards)."""
    principal_scopes: set[str] = set(principal.scopes)
    for role in principal.roles:
        principal_scopes.update(SCOPE_PRESETS.get(role, []))
    if "*" in principal_scopes:
        return True
    alias = SCOPE_ALIASES.get(required_scope)
    return any(
        s == required_scope
        or (alias is not None and s == alias)
        or (s.endswith("*") and (required_scope.startswith(s[:-1]) or (alias is not None and alias.startswith(s[:-1]))))
        for s in principal_scopes
    )


def _resolve_principal_optional(request: Request, header_token: str | None) -> Principal | None:
    """Resolve a principal from a Bearer token or session WITHOUT raising.

    Mirrors the credential resolution in ``require_principal`` but returns
    ``None`` instead of a 401 when no valid credential is present. Used by
    ``require_fleet_peer`` so the fleet-token path can be tried as a fallback.
    """
    if header_token and header_token.startswith("Bearer "):
        raw_token = header_token.replace("Bearer ", "")
        prin = identity_manager.verify_token(raw_token)
        if prin:
            return prin

    session = _optional_session(request)
    if session:
        principal_id = session.get("principal_id")
        session_id = session.get("session_id")
        if principal_id and principal_id in identity_manager.principals:
            if session_id and not sm.touch_session(session_id):
                return None
            return identity_manager.principals[principal_id]
    return None


@functools.cache
def require_scope(required_scope: str):
    def checker(
        request: Request,
        header_token: str | None = Depends(auth_header),
        cookie_token: str | None = Depends(auth_cookie),
    ) -> Principal:
        overrides = getattr(request.app, "dependency_overrides", {})
        if require_principal in overrides:
            override_fn = overrides[require_principal]
            prin = override_fn() if callable(override_fn) else override_fn
            if isinstance(prin, Principal):
                if principal_has_scope(prin, required_scope):
                    return prin
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Authorization failed",
                        "required_scope": required_scope,
                        "principal": prin.id,
                    },
                )
        if require_orchestrator_peer in overrides:
            return Principal(
                id="test-orchestrator",
                type="bot",
                name="Test Orchestrator",
                roles=["admin"],
            )
        if require_fleet_peer in overrides:
            return Principal(id="test-peer", type="bot", name="Test Peer", roles=["fleet-peer"])

        # 1. Bearer token or session principal
        prin = _resolve_principal_optional(request, header_token)
        hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
        if prin is None and hub_token and header_token and header_token.startswith("Bearer "):
            if hmac.compare_digest(header_token[7:], hub_token):
                prin = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
        if prin is None and _loopback_auth_enabled() and _is_loopback_request(request):
            prin = _loopback_principal()
        if prin is None and required_scope == "staff.read" and not hub_token:
            prin = Principal(
                id="anonymous:tailnet",
                type="human",
                name="Anonymous Tailnet",
                roles=["viewer"],
            )

        if prin is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if principal_has_scope(prin, required_scope):
            return prin
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Authorization failed",
                "required_scope": required_scope,
                "principal": prin.id,
            },
        )

    return checker


def require_fleet_peer(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> str:
    """Authenticate an intra-fleet caller for hub-reachable fleet routes (#922).

    A request is accepted when EITHER:
      - it carries valid operator credentials (a principal resolved from a
        service token or session), OR
      - it presents ``Authorization: Bearer <HUB_FLEET_TOKEN>`` matching the
        hub's configured ``HUB_FLEET_TOKEN`` (constant-time compare).

    Policy decision (documented in docs/runbooks/hub-credentials.md): when
    ``HUB_FLEET_TOKEN`` is UNSET on this node, fleet reads are tailnet-public —
    the dependency is a no-op so single-node and token-less deployments keep
    working. When the token IS set, the fleet trust boundary is enforced and
    an unauthenticated caller gets 401.

    Returns a short principal/peer label for logging; never the token itself.
    """
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")

    # A valid operator principal is always accepted.
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        return f"principal:{principal.id}"

    # No token configured → fleet reads are tailnet-public (backward compatible).
    if not hub_token:
        return "anonymous:tailnet"

    # Token configured → require a constant-time match of the fleet bearer token.
    if header_token and header_token.startswith("Bearer "):
        presented = header_token[len("Bearer ") :]
        if hmac.compare_digest(presented, hub_token):
            return "fleet-peer"

    raise HTTPException(status_code=401, detail="Fleet authentication required")


def require_orchestrator_peer(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> str:
    """Authenticate an orchestrator caller for lease/release/queue endpoints (#1173).

    State-changing orchestrator operations (lease, release, queue control) require
    authentication regardless of whether ``HUB_FLEET_TOKEN`` is configured:
      1. An operator principal (resolved from service token or session) is always accepted.
      2. If ``HUB_FLEET_TOKEN`` is configured and matches the presented bearer token
         (constant-time compare), it is accepted as a fleet peer.
      3. If ``DASHBOARD_LOOPBACK_AUTH=1`` and the request originates from loopback,
         it is accepted so a node's local Conductor agent functions without credentials.

    Unauthenticated remote callers (e.g. over the tailnet) are strictly rejected with 401.
    """
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        return f"principal:{principal.id}"

    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if hub_token and header_token and header_token.startswith("Bearer "):
        presented = header_token[len("Bearer ") :]
        if hmac.compare_digest(presented, hub_token):
            return "fleet-peer"

    if _loopback_auth_enabled() and _is_loopback_request(request):
        return "loopback-dev"

    raise HTTPException(status_code=401, detail="Authentication required")


def resolve_perimeter_principal(request: Request) -> Principal | None:
    """Resolve the calling principal for the structural auth perimeter (#924).

    This mirrors the credential resolution performed by :func:`require_principal`
    but is callable from ASGI middleware (where the route-dependency machinery is
    not yet available). It returns ``None`` instead of raising so the middleware
    can decide the response.

    Resolution order matches ``require_principal``:
      1. ``Authorization: Bearer <service-token>``.
      2. Session cookie (requires SessionMiddleware to have run first).
      3. Loopback development admin, only when ``DASHBOARD_LOOPBACK_AUTH=1`` and
         the transport peer is a loopback address.
    """
    header_token = request.headers.get("Authorization")
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        return principal

    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if hub_token and header_token and header_token.startswith("Bearer "):
        presented = header_token[len("Bearer ") :]
        if hmac.compare_digest(presented, hub_token):
            return Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])

    if _loopback_auth_enabled() and _is_loopback_request(request):
        return _loopback_principal()

    return None


def format_caller(principal: Principal) -> str:
    """Return a short identifier string for logging and requested_by fields."""
    if principal.id in (
        "fleet-peer",
        "__loopback__",
        "loopback-dev",
        "test-orchestrator",
        "test-peer",
    ):
        return principal.id
    return f"principal:{principal.id}"
