"""OAuth public-origin validation against fleet hub registry (#2075)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fleet_autoconfig import (  # noqa: E402
    dashboard_url_to_tailscale_https_origin,
    hub_public_origins_from_registry,
    resolve_hub_public_origin_from_registry,
)
from oauth_config import (  # noqa: E402
    OAUTH_CALLBACK_PATH,
    OAuthConfig,
    allowed_public_origins,
)

_DESK_ORIGIN = "https://deskcomputer.tail2bbcc7.ts.net"
_DESK_CALLBACK = f"{_DESK_ORIGIN}{OAUTH_CALLBACK_PATH}"
_OGLAPTOP_ORIGIN = "https://oglaptop.tail2bbcc7.ts.net"
_OGLAPTOP_CALLBACK = f"{_OGLAPTOP_ORIGIN}{OAUTH_CALLBACK_PATH}"


def _credentials_env(**updates: str) -> dict[str, str]:
    env = {
        "GITHUB_CLIENT_ID": "12345678",
        "GITHUB_CLIENT_SECRET": "a" * 32,
        "GITHUB_ORG": "D-sorganization",
        "SESSION_SECRET": "b" * 32,
        "DASHBOARD_TLS": "1",
    }
    env.update(updates)
    return env


def test_dashboard_url_to_tailscale_https_origin_strips_port() -> None:
    assert (
        dashboard_url_to_tailscale_https_origin("http://deskcomputer.tail2bbcc7.ts.net:8321")
        == _DESK_ORIGIN
    )


def test_hub_public_origins_from_registry_lists_only_hubs() -> None:
    registry = {
        "machines": [
            {
                "name": "DeskComputer",
                "role": "hub",
                "dashboard_url": "http://deskcomputer.tail2bbcc7.ts.net:8321",
            },
            {
                "name": "OGLaptop",
                "role": "node",
                "dashboard_url": "http://oglaptop.tail2bbcc7.ts.net:8321",
            },
        ]
    }
    assert hub_public_origins_from_registry(registry) == (_DESK_ORIGIN,)
    assert resolve_hub_public_origin_from_registry(registry) == _DESK_ORIGIN


def test_deskcomputer_origin_is_ready_with_matching_callback() -> None:
    env = _credentials_env(
        DASHBOARD_PUBLIC_ORIGIN=_DESK_ORIGIN,
        GITHUB_OAUTH_CALLBACK_URL=_DESK_CALLBACK,
    )
    diagnostic = OAuthConfig.from_env(env).diagnostic()
    assert diagnostic["ready"] is True


def test_oglaptop_origin_blocked_without_allowlist() -> None:
    env = _credentials_env(
        DASHBOARD_PUBLIC_ORIGIN=_OGLAPTOP_ORIGIN,
        GITHUB_OAUTH_CALLBACK_URL=_OGLAPTOP_CALLBACK,
    )
    diagnostic = OAuthConfig.from_env(env).diagnostic()
    assert diagnostic["ready"] is False
    assert "public_origin_mismatch" in diagnostic["reason"]


def test_oglaptop_origin_allowed_via_explicit_allowlist_env() -> None:
    env = _credentials_env(
        DASHBOARD_PUBLIC_ORIGIN=_OGLAPTOP_ORIGIN,
        GITHUB_OAUTH_CALLBACK_URL=_OGLAPTOP_CALLBACK,
        DASHBOARD_OAUTH_ALLOWED_ORIGINS=_OGLAPTOP_ORIGIN,
    )
    diagnostic = OAuthConfig.from_env(env).diagnostic()
    assert diagnostic["ready"] is True


def test_callback_must_match_origin_plus_path() -> None:
    env = _credentials_env(
        DASHBOARD_PUBLIC_ORIGIN=_DESK_ORIGIN,
        GITHUB_OAUTH_CALLBACK_URL="https://deskcomputer.tail2bbcc7.ts.net/api/auth/callback/evil",
    )
    diagnostic = OAuthConfig.from_env(env).diagnostic()
    assert diagnostic["ready"] is False
    assert "callback_url_mismatch" in diagnostic["reason"]


def test_from_env_defaults_origin_and_callback_from_registry_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASHBOARD_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CALLBACK_URL", raising=False)
    cfg = OAuthConfig.from_env(_credentials_env())
    assert cfg.public_origin == _DESK_ORIGIN
    assert cfg.callback_url == _DESK_CALLBACK
    assert cfg.diagnostic()["ready"] is True


def test_allowed_public_origins_includes_registry_hub_and_allowlist() -> None:
    origins = allowed_public_origins(
        {"DASHBOARD_OAUTH_ALLOWED_ORIGINS": _OGLAPTOP_ORIGIN},
    )
    assert _DESK_ORIGIN in origins
    assert _OGLAPTOP_ORIGIN in origins
