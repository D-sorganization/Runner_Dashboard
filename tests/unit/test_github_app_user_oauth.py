"""GitHub App user-to-server OAuth refresh persistence (#2075).

GitHub documents expiring user access tokens (~8 h) and refresh tokens (~6 months):
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from github_app_user_oauth import (  # noqa: E402
    UserOAuthRevokedError,
    UserOAuthTransientError,
    aggregate_operator_auth_health,
    apply_authorization_tokens,
    delete_credentials,
    ensure_fresh_access_token,
    load_credentials,
    save_credentials,
)
from oauth_config import OAUTH_CALLBACK_PATH  # noqa: E402

_HUB_ORIGIN = "https://deskcomputer.tail2bbcc7.ts.net"
_HUB_CALLBACK = f"{_HUB_ORIGIN}{OAUTH_CALLBACK_PATH}"

_FAKE_ACCESS = "ghu_" + "A" * 36
_FAKE_REFRESH = "ghr_" + "B" * 36
_FAKE_REFRESH2 = "ghr_" + "C" * 36


def _oauth_env() -> dict[str, str]:
    return {
        "GITHUB_CLIENT_ID": "Iv1.githubAppClient",
        "GITHUB_CLIENT_SECRET": "x" * 40,  # pragma: allowlist secret
        "GITHUB_ORG": "D-sorganization",
        "DASHBOARD_PUBLIC_ORIGIN": _HUB_ORIGIN,
        "GITHUB_OAUTH_CALLBACK_URL": _HUB_CALLBACK,
        "SESSION_SECRET": "s" * 64,  # pragma: allowlist secret
        "DASHBOARD_TLS": "1",
    }


@pytest.fixture
def oauth_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "user_oauth.json"
    monkeypatch.setenv("DASHBOARD_GITHUB_USER_OAUTH_STORE", str(store))
    for key, val in _oauth_env().items():
        monkeypatch.setenv(key, val)
    return store


def test_store_encrypts_tokens_at_rest(oauth_store: Path) -> None:
    now = time.time()
    save_credentials(
        "dieter",
        github_login="dieter-user",
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        access_token_expires_at=now + 3600,
        refresh_token_expires_at=now + 86400 * 30,
    )
    raw = oauth_store.read_text(encoding="utf-8")
    assert _FAKE_ACCESS not in raw
    assert _FAKE_REFRESH not in raw
    loaded = load_credentials("dieter")
    assert loaded is not None
    assert loaded.access_token == _FAKE_ACCESS
    assert loaded.refresh_token == _FAKE_REFRESH


def test_apply_authorization_tokens_persists_expiry_fields(oauth_store: Path) -> None:
    now = time.time()
    apply_authorization_tokens(
        "dieter",
        github_login="dieter-user",
        token_response={
            "access_token": _FAKE_ACCESS,
            "refresh_token": _FAKE_REFRESH,
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
        },
        now=now,
    )
    cred = load_credentials("dieter")
    assert cred is not None
    assert cred.access_token_expires_at == now + 28800
    assert cred.refresh_token_expires_at == now + 15897600


def test_ensure_fresh_refreshes_expired_access_token(oauth_store: Path) -> None:
    now = time.time()
    save_credentials(
        "dieter",
        github_login="dieter-user",
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        access_token_expires_at=now - 10,
        refresh_token_expires_at=now + 86400,
    )
    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "access_token": "ghu_" + "D" * 36,
        "refresh_token": _FAKE_REFRESH2,
        "expires_in": 28800,
        "refresh_token_expires_in": 15897600,
    }
    with patch("github_app_user_oauth.httpx.post", return_value=token_resp) as post:
        ensure_fresh_access_token("dieter", skew_seconds=300)
    post.assert_called_once()
    body = post.call_args.kwargs.get("data") or post.call_args[1].get("data")
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == _FAKE_REFRESH
    cred = load_credentials("dieter")
    assert cred is not None
    assert cred.refresh_token == _FAKE_REFRESH2


def test_ensure_fresh_raises_when_refresh_revoked(oauth_store: Path) -> None:
    now = time.time()
    save_credentials(
        "dieter",
        github_login="dieter-user",
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        access_token_expires_at=now - 10,
        refresh_token_expires_at=now + 86400,
    )
    token_resp = MagicMock()
    token_resp.status_code = 400
    token_resp.json.return_value = {"error": "bad_refresh_token"}
    with patch("github_app_user_oauth.httpx.post", return_value=token_resp):
        with pytest.raises(UserOAuthRevokedError):
            ensure_fresh_access_token("dieter", skew_seconds=300)
    assert load_credentials("dieter") is None


def test_aggregate_health_surfaces_expiry_without_secrets(oauth_store: Path) -> None:
    now = time.time()
    save_credentials(
        "dieter",
        github_login="dieter-user",
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        access_token_expires_at=now + 3600,
        refresh_token_expires_at=now + 86400 * 5,
    )
    health = aggregate_operator_auth_health(now=now)
    assert health["operators"]
    row = health["operators"][0]
    assert row["principal_id"] == "dieter"
    assert row["github_login"] == "dieter-user"
    assert _FAKE_ACCESS not in json.dumps(row)
    assert _FAKE_REFRESH not in json.dumps(row)
    assert row["refresh_token_expires_in_seconds"] == 86400 * 5


def test_transient_github_error_preserves_refresh_token(oauth_store: Path) -> None:
    now = time.time()
    save_credentials(
        "dieter",
        github_login="dieter-user",
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        access_token_expires_at=now - 10,
        refresh_token_expires_at=now + 86400,
    )
    token_resp = MagicMock()
    token_resp.status_code = 503
    token_resp.json.return_value = {"error": "service_unavailable"}
    with patch("github_app_user_oauth.httpx.post", return_value=token_resp):
        with pytest.raises(UserOAuthTransientError):
            ensure_fresh_access_token("dieter", skew_seconds=300)
    cred = load_credentials("dieter")
    assert cred is not None
    assert cred.refresh_token == _FAKE_REFRESH


def test_delete_credentials_on_logout(oauth_store: Path) -> None:
    now = time.time()
    save_credentials(
        "dieter",
        github_login="u",
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        access_token_expires_at=now + 1,
        refresh_token_expires_at=now + 1,
    )
    delete_credentials("dieter")
    assert load_credentials("dieter") is None
