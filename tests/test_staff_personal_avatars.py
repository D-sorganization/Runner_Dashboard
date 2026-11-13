"""Tests for safe personal avatar upload and reset contract (#3500)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

ROLES_YAML_BARB = "name: barb\ntitle: Barb\nsummary: Fleet secretary.\n"
ROLES_YAML_CODEX = "name: codex-worker\ntitle: Codex Worker\nsummary: Lane worker.\n"

_VALID_32X32_WEBP = (
    b"RIFF\x1e\x00\x00\x00WEBPVP8 \x12\x00\x00\x00\x00\x00\x00\x9d\x01\x2a\x20\x00\x20\x00"
    + b"\x00" * 8
)
_VALID_32X32_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00 \x00\x00\x00 \x08\x06\x00\x00\x005\xaf\x06\x19"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_BIG_WEBP = (
    b"RIFF\x1e\x00\x00\x00WEBPVP8 \x12\x00\x00\x00\x00\x00\x00\x9d\x01\x2a"
    + struct.pack("<HH", 4096, 4096)
    + b"\x00" * 8
)
_BIG_BODY = b"0" * (1536 * 1024)


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "barb.yml").write_text(ROLES_YAML_BARB)
    (roles / "codex-worker.yml").write_text(ROLES_YAML_CODEX)
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles))
    (tmp_path / "avatars").mkdir(parents=True, exist_ok=True)
    (tmp_path / "avatars" / "barb.webp").write_bytes(_VALID_32X32_WEBP)
    monkeypatch.setenv("STAFF_PRESENTATION_PREFS_DB", str(tmp_path / "prefs.sqlite3"))
    monkeypatch.setenv("STAFF_PERSONAL_AVATARS_DIR", str(tmp_path / "custom_avatars"))

    import staff.personal_avatars as avatars_mod
    import staff.presentation_prefs as prefs_mod
    import staff.roles as roles_mod

    roles_mod.clear_roles_cache()
    prefs_mod._STORE, avatars_mod._STORE = None, None
    yield
    roles_mod.clear_roles_cache()
    prefs_mod._STORE, avatars_mod._STORE = None, None


def _client_as(operator_id: str) -> Any:
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    principal = Principal(
        id=operator_id,
        type="human",
        name=operator_id,
        scopes=["staff.read", "staff.write"],
    )
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


@pytest.fixture
def client(test_env: Any) -> Any:
    c = _client_as("operator-a")
    try:
        yield c
    finally:
        from server import app

        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("payload", "mime"),
    [(_VALID_32X32_WEBP, "image/webp"), (_VALID_32X32_PNG, "image/png")],
)
def test_upload_serve_and_reset_custom_avatar(
    client: Any, payload: bytes, mime: str
) -> None:
    up = client.put(
        "/api/v1/staff/avatars/custom/barb",
        content=payload,
        headers={"Content-Type": mime},
    )
    assert up.status_code == 200, up.text
    url = up.json()["avatar_url"]
    assert (
        client.get("/api/v1/staff/presentation-prefs").json()["avatar_overrides"][
            "barb"
        ]
        == url
    )
    serve = client.get(url)
    assert serve.status_code == 200 and serve.headers["content-type"].startswith(mime)
    reset = client.post("/api/v1/staff/avatars/custom/barb/reset")
    assert reset.status_code == 200 and reset.json()["reset"] is True
    assert (
        "barb"
        not in client.get("/api/v1/staff/presentation-prefs").json()["avatar_overrides"]
    )


def test_cross_operator_isolation(test_env: Any) -> None:
    from server import app

    ca = _client_as("operator-a")
    url = ca.put("/api/v1/staff/avatars/custom/barb", content=_VALID_32X32_WEBP).json()[
        "avatar_url"
    ]
    app.dependency_overrides.clear()
    cb = _client_as("operator-b")
    assert cb.get(url).status_code == 403
    assert cb.get("/api/v1/staff/presentation-prefs").json()["avatar_overrides"] == {}
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("role", "content", "expected_status"),
    [
        ("barb", b"not an image", (422,)),
        ("barb", _VALID_32X32_WEBP[:16], (422,)),
        ("barb", _BIG_WEBP, (422,)),
        ("barb", _BIG_BODY, (413, 422)),
        ("ghost-role", _VALID_32X32_WEBP, (422,)),
        ("..%2F..%2Fetc%2Fpasswd", _VALID_32X32_WEBP, (404, 405, 422)),
    ],
)
def test_rejections(
    client: Any, role: str, content: bytes, expected_status: tuple[int, ...]
) -> None:
    assert (
        client.put(f"/api/v1/staff/avatars/custom/{role}", content=content).status_code
        in expected_status
    )
    if role.startswith(".."):
        assert client.get(
            f"/api/v1/staff/avatars/personal/operator-a/{role}"
        ).status_code in (404, 422)


def test_stale_revision_conflict(client: Any) -> None:
    u1 = client.put(
        "/api/v1/staff/avatars/custom/barb?expected_revision=0",
        content=_VALID_32X32_WEBP,
    )
    assert u1.status_code == 200
    assert (
        client.put(
            "/api/v1/staff/avatars/custom/barb?expected_revision=0",
            content=_VALID_32X32_PNG,
        ).status_code
        == 409
    )
    assert (
        client.get(u1.json()["avatar_url"])
        .headers["content-type"]
        .startswith("image/webp")
    )
