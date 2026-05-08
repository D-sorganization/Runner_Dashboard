"""Tests for loopback bypass removal (issue #315).

Verifies that:
- Requests from 127.0.0.1 with a spoofed X-Forwarded-For header are rejected.
- Unauthenticated loopback requests require a Bearer token or session.
- The __loopback__ synthetic admin principal no longer exists.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

from fastapi import HTTPException  # noqa: E402
from identity import require_principal  # noqa: E402


def _mock_request(host: str, *, forwarded_for: str | None = None) -> MagicMock:
    """Return a minimal mock Request with the given peer address."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = host
    req.headers = MagicMock()
    req.headers.get = MagicMock(side_effect=lambda k, d=None: forwarded_for if k == "X-Forwarded-For" else d)
    req.state = MagicMock()
    req.session = {}
    return req


# ---------------------------------------------------------------------------
# 1. Spoofed X-Forwarded-For: 127.0.0.1 from a non-loopback peer → 401
# ---------------------------------------------------------------------------


def test_spoofed_xff_loopback_is_rejected() -> None:
    """A request from 10.0.0.1 with X-Forwarded-For: 127.0.0.1 must get 401.

    Previously the loopback bypass read client.host which a reverse proxy could
    set to 127.0.0.1 for any upstream caller.  After #315 the bypass is gone
    entirely so neither real nor spoofed loopback addresses grant access.
    """
    req = _mock_request("10.0.0.1", forwarded_for="127.0.0.1")
    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401
    assert "Authentication required" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# 2. Real loopback peer without credentials → 401
# ---------------------------------------------------------------------------


def test_loopback_peer_without_credentials_is_rejected() -> None:
    """A real 127.0.0.1 connection with no token or session must get 401.

    The __loopback__ admin bypass has been removed; loopback callers must
    present a valid Bearer token or session cookie just like any other caller.
    """
    req = _mock_request("127.0.0.1")
    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401


def test_ipv6_loopback_peer_without_credentials_is_rejected() -> None:
    """A real ::1 connection with no token or session must get 401."""
    req = _mock_request("::1")
    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=req, header_token=None, cookie_token=None)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 3. __loopback__ principal no longer exists in the module
# ---------------------------------------------------------------------------


def test_loopback_admin_principal_removed() -> None:
    """The _LOOPBACK_ADMIN synthetic principal must no longer be defined."""
    import identity as id_mod

    assert not hasattr(id_mod, "_LOOPBACK_ADMIN"), (
        "_LOOPBACK_ADMIN is still exported; the loopback bypass was not fully removed"
    )
