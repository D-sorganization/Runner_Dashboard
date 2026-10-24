"""PUT/DELETE staff role avatar uploads (#2680 slice 3)."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.unit.test_staff_roles import _VALID_ROLE_DICT

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.42", 52042)

MINIMAL_WEBP = (
    b"RIFF$\x00\x00\x00WEBPVP8 \x18\x00\x00\x00\xd0\x01\x00\x9d\x01*\x01\x00\x01\x00>"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)


def _staff_tree(tmp_path: Path, avatar_rel: str = "cartographer.webp") -> Path:
    staff = tmp_path / "staff"
    roles_dir = staff / "roles"
    avatars_dir = staff / "avatars"
    roles_dir.mkdir(parents=True)
    avatars_dir.mkdir(parents=True)
    role = dict(_VALID_ROLE_DICT)
    role["avatar"] = avatar_rel
    (avatars_dir / avatar_rel).write_bytes(MINIMAL_WEBP)
    (roles_dir / "cartographer.yml").write_text(yaml.dump(role), encoding="utf-8")
    return roles_dir


def _png_128() -> bytes:
    from tests.staff.test_role_avatar_image import _png_with_dims

    return _png_with_dims(128, 128)


@pytest.fixture
def operator_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(_staff_tree(tmp_path)))
    monkeypatch.setenv("RD_AVATAR_DIR", str(tmp_path / "custom-avatars"))
    id_dir = tmp_path / "identity-op"
    id_dir.mkdir(parents=True)
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    from identity import Principal, TokenRecord, identity_manager  # noqa: PLC0415
    from staff import roles as roles_mod  # noqa: PLC0415
    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    store_mod.reset_store()
    runner_mod.reset_runner()
    from routers import staff as staff_router  # noqa: PLC0415
    from routers import staff_v1 as staff_v1_router  # noqa: PLC0415

    monkeypatch.setattr(staff_router, "provider_versions", lambda _a: {})
    monkeypatch.setattr(staff_v1_router, "provider_versions", lambda _a: {})
    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {
        "operator-user": Principal(id="operator-user", type="human", name="Operator", roles=["operator"]),
    }
    token = "operator_avatar_write_token"  # pragma: allowlist secret
    thash = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    identity_manager.tokens = [
        TokenRecord(
            token_hash=thash,
            principal_id="operator-user",
            name="operator-test",
            created_at=now,
            expires_at=now + 86400,
        ),
    ]
    from server import app  # noqa: PLC0415

    return TestClient(
        app,
        raise_server_exceptions=False,
        client=_REMOTE,
        headers={**_XHR, "Authorization": f"Bearer {token}"},
    )


@pytest.mark.unit
def test_put_stores_custom_avatar(operator_client: TestClient) -> None:
    body = _png_128()
    res = operator_client.put("/api/v1/staff/roles/cartographer/avatar", content=body)
    assert res.status_code == 200, res.text
    assert operator_client.get("/api/v1/staff/roles/cartographer/avatar").content == body


@pytest.mark.unit
def test_delete_restores_default(operator_client: TestClient) -> None:
    operator_client.put("/api/v1/staff/roles/cartographer/avatar", content=_png_128())
    assert operator_client.delete("/api/v1/staff/roles/cartographer/avatar").status_code == 204
    assert operator_client.get("/api/v1/staff/roles/cartographer/avatar").content == MINIMAL_WEBP


@pytest.mark.unit
def test_put_without_csrf_is_403(operator_client: TestClient) -> None:
    auth = operator_client.headers.get("Authorization")
    bare = TestClient(
        operator_client.app,
        raise_server_exceptions=False,
        client=_REMOTE,
        headers={"Authorization": auth},
    )
    assert bare.put("/api/v1/staff/roles/cartographer/avatar", content=_png_128()).status_code == 403
