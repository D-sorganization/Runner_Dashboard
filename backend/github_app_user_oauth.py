"""Persist and refresh GitHub App user-to-server OAuth tokens (#2075).

Fleet browser sign-in uses the same GitHub App as installation-token minting,
with expiring user access tokens and rotating refresh tokens stored encrypted
at rest. See GitHub's guide on refreshing user access tokens (8 h access,
~6 month refresh):
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from cryptography.fernet import Fernet, InvalidToken
from oauth_config import OAuthConfig

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows dev hosts
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("dashboard.github_app_user_oauth")

_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_DEFAULT_ACCESS_TTL_SECONDS = 8 * 3600
_DEFAULT_REFRESH_TTL_SECONDS = 15897600  # ~6 months per GitHub docs
_DEFAULT_REFRESH_SKEW_SECONDS = 300.0
_REVOKED_REFRESH_ERRORS = frozenset(
    {
        "bad_refresh_token",
        "invalid_grant",
        "incorrect_client_credentials",
        "access_denied",
    }
)
_RefreshOutcome = Literal["ok", "revoked", "transient", "malformed"]


class UserOAuthRevokedError(RuntimeError):
    """Refresh token rejected by GitHub; stored credentials were cleared."""


class UserOAuthTransientError(RuntimeError):
    """GitHub token endpoint temporarily unavailable; credentials preserved."""


class OperatorAuthHealthRow(TypedDict):
    principal_id: str
    github_login: str
    access_token_expires_at: float
    refresh_token_expires_at: float
    access_token_expires_in_seconds: float
    refresh_token_expires_in_seconds: float
    status: str


class OperatorAuthHealth(TypedDict):
    operators: list[OperatorAuthHealthRow]
    earliest_refresh_token_expires_at: float | None


@dataclass
class UserOAuthCredentials:
    principal_id: str
    github_login: str
    access_token: str
    refresh_token: str
    access_token_expires_at: float
    refresh_token_expires_at: float
    updated_at: float


def _store_path() -> Path:
    raw = os.environ.get("DASHBOARD_GITHUB_USER_OAUTH_STORE", "").strip()
    if raw:
        return Path(raw).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard"
    return base / "github_user_oauth.json"


def _store_lock_path(store_path: Path) -> Path:
    return store_path.with_name(store_path.name + ".lock")


def _principal_lock_path(store_path: Path, principal_id: str) -> Path:
    digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:24]
    return store_path.with_name(f"oauth-principal-{digest}.lock")


@contextmanager
def _inter_process_lock(lock_path: Path):
    """Exclusive flock for cross-worker serialization (issue #2075 review)."""
    if fcntl is None:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        handle.close()


@contextmanager
def _store_lock(store_path: Path):
    with _inter_process_lock(_store_lock_path(store_path)):
        yield


@contextmanager
def _principal_lock(store_path: Path, principal_id: str):
    with _inter_process_lock(_principal_lock_path(store_path, principal_id)):
        yield


def _fernet() -> Fernet:
    from dashboard_config import SESSION_SECRET  # noqa: PLC0415

    secret = SESSION_SECRET.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("corrupt encrypted OAuth store") from exc


def _load_blob(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_blob(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.chmod(tmp_path_str, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path_str, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _record_to_blob(cred: UserOAuthCredentials) -> dict[str, Any]:
    payload = asdict(cred)
    payload["access_token"] = _encrypt(cred.access_token)
    payload["refresh_token"] = _encrypt(cred.refresh_token)
    return payload


def _record_from_blob(principal_id: str, blob: dict[str, Any]) -> UserOAuthCredentials:
    return UserOAuthCredentials(
        principal_id=principal_id,
        github_login=str(blob.get("github_login") or ""),
        access_token=_decrypt(str(blob["access_token"])),
        refresh_token=_decrypt(str(blob["refresh_token"])),
        access_token_expires_at=float(blob.get("access_token_expires_at") or 0),
        refresh_token_expires_at=float(blob.get("refresh_token_expires_at") or 0),
        updated_at=float(blob.get("updated_at") or 0),
    )


def _credentials_from_store(store: dict[str, Any], principal_id: str) -> UserOAuthCredentials | None:
    row = store.get(principal_id)
    if not isinstance(row, dict):
        return None
    try:
        return _record_from_blob(principal_id, row)
    except (KeyError, ValueError, TypeError):
        log.warning("dropping corrupt github user oauth row for principal=%s", principal_id)
        return None


def load_credentials(principal_id: str, path: Path | None = None) -> UserOAuthCredentials | None:
    target = path or _store_path()
    with _store_lock(target):
        return _credentials_from_store(_load_blob(target), principal_id)


def save_credentials(
    principal_id: str,
    *,
    github_login: str,
    access_token: str,
    refresh_token: str,
    access_token_expires_at: float,
    refresh_token_expires_at: float,
    path: Path | None = None,
    now: float | None = None,
) -> UserOAuthCredentials:
    cred = UserOAuthCredentials(
        principal_id=principal_id,
        github_login=github_login,
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_at=access_token_expires_at,
        refresh_token_expires_at=refresh_token_expires_at,
        updated_at=now if now is not None else time.time(),
    )
    target = path or _store_path()
    with _store_lock(target):
        store = _load_blob(target)
        store[principal_id] = _record_to_blob(cred)
        _save_blob(target, store)
    return cred


def delete_credentials(principal_id: str, path: Path | None = None) -> None:
    target = path or _store_path()
    with _store_lock(target):
        store = _load_blob(target)
        if principal_id not in store:
            return
        store.pop(principal_id, None)
        _save_blob(target, store)


def apply_authorization_tokens(
    principal_id: str,
    *,
    github_login: str,
    token_response: dict[str, Any],
    now: float | None = None,
) -> UserOAuthCredentials:
    """Persist tokens from the initial code exchange or a refresh response."""
    ts = now if now is not None else time.time()
    access = str(token_response.get("access_token") or "")
    refresh = str(token_response.get("refresh_token") or "")
    if not access or not refresh:
        raise ValueError("GitHub token response missing access_token or refresh_token")
    access_ttl = int(token_response.get("expires_in") or _DEFAULT_ACCESS_TTL_SECONDS)
    refresh_ttl = int(token_response.get("refresh_token_expires_in") or _DEFAULT_REFRESH_TTL_SECONDS)
    return save_credentials(
        principal_id,
        github_login=github_login,
        access_token=access,
        refresh_token=refresh,
        access_token_expires_at=ts + access_ttl,
        refresh_token_expires_at=ts + refresh_ttl,
        now=ts,
    )


def _refresh_request_data(config: OAuthConfig, refresh_token: str) -> dict[str, str]:
    config.require_ready()
    return {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }


def _classify_refresh_response(resp: httpx.Response) -> tuple[dict[str, Any], _RefreshOutcome]:
    try:
        body = resp.json()
    except ValueError:
        return {}, "malformed"
    if not isinstance(body, dict):
        return {}, "malformed"
    if resp.status_code < 400 and body.get("access_token") and body.get("refresh_token"):
        return body, "ok"
    if resp.status_code in (429, 500, 502, 503, 504) or resp.status_code >= 500:
        return body, "transient"
    err = str(body.get("error") or "").lower()
    if err in _REVOKED_REFRESH_ERRORS:
        return body, "revoked"
    if resp.status_code in (400, 401, 403):
        return body, "revoked"
    if resp.status_code >= 400:
        return body, "transient"
    return body, "malformed"


def refresh_user_access_token(
    cred: UserOAuthCredentials,
    config: OAuthConfig | None = None,
    *,
    now: float | None = None,
    store_path: Path | None = None,
) -> UserOAuthCredentials:
    """Exchange the refresh token; GitHub rotates the refresh token on each use."""
    cfg = config or OAuthConfig.from_env()
    target = store_path or _store_path()
    ts = now if now is not None else time.time()
    if cred.refresh_token_expires_at <= ts:
        delete_credentials(cred.principal_id, path=target)
        raise UserOAuthRevokedError("refresh token expired")
    resp = httpx.post(
        _GITHUB_TOKEN_URL,
        data=_refresh_request_data(cfg, cred.refresh_token),
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    body, outcome = _classify_refresh_response(resp)
    if outcome == "ok":
        return apply_authorization_tokens(
            cred.principal_id,
            github_login=cred.github_login,
            token_response=body,
            now=ts,
        )
    if outcome == "transient":
        detail = str(body.get("error") or f"GitHub token endpoint HTTP {resp.status_code}")
        raise UserOAuthTransientError(detail)
    if outcome == "revoked":
        delete_credentials(cred.principal_id, path=target)
        raise UserOAuthRevokedError(str(body.get("error") or "refresh rejected"))
    raise UserOAuthTransientError("malformed GitHub refresh response")


def ensure_fresh_access_token(
    principal_id: str,
    *,
    skew_seconds: float = _DEFAULT_REFRESH_SKEW_SECONDS,
    config: OAuthConfig | None = None,
    now: float | None = None,
    path: Path | None = None,
) -> UserOAuthCredentials | None:
    """Refresh the user access token when it is near expiry."""
    target = path or _store_path()
    ts = now if now is not None else time.time()
    with _principal_lock(target, principal_id):
        with _store_lock(target):
            cred = _credentials_from_store(_load_blob(target), principal_id)
            if cred is None:
                return None
            if cred.access_token_expires_at > ts + skew_seconds:
                return cred
        refreshed = refresh_user_access_token(cred, config=config, now=ts, store_path=target)
        with _store_lock(target):
            latest = _credentials_from_store(_load_blob(target), principal_id)
            if latest is not None and latest.access_token_expires_at > ts + skew_seconds:
                return latest
        return refreshed


def _health_status(cred: UserOAuthCredentials, now: float) -> str:
    if cred.refresh_token_expires_at <= now:
        return "refresh_expired"
    if cred.refresh_token_expires_at <= now + 86400 * 7:
        return "refresh_expiring_soon"
    if cred.access_token_expires_at <= now:
        return "access_expired"
    if cred.access_token_expires_at <= now + 3600:
        return "access_expiring_soon"
    return "ok"


def aggregate_operator_auth_health(now: float | None = None) -> OperatorAuthHealth:
    """Redacted operator OAuth health for Barb and ``/api/health``."""
    ts = now if now is not None else time.time()
    target = _store_path()
    with _store_lock(target):
        store = _load_blob(target)
    rows: list[OperatorAuthHealthRow] = []
    earliest_refresh: float | None = None
    for principal_id, blob in sorted(store.items()):
        if not isinstance(blob, dict):
            continue
        try:
            cred = _record_from_blob(str(principal_id), blob)
        except (KeyError, ValueError, TypeError):
            continue
        if earliest_refresh is None or cred.refresh_token_expires_at < earliest_refresh:
            earliest_refresh = cred.refresh_token_expires_at
        rows.append(
            {
                "principal_id": cred.principal_id,
                "github_login": cred.github_login,
                "access_token_expires_at": cred.access_token_expires_at,
                "refresh_token_expires_at": cred.refresh_token_expires_at,
                "access_token_expires_in_seconds": max(0.0, cred.access_token_expires_at - ts),
                "refresh_token_expires_in_seconds": max(0.0, cred.refresh_token_expires_at - ts),
                "status": _health_status(cred, ts),
            }
        )
    return {"operators": rows, "earliest_refresh_token_expires_at": earliest_refresh}
