"""Persona avatar API (#2680)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.unit.test_staff_roles import _VALID_ROLE_DICT

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.42", 52042)

# Minimal lossy WebP (1x1); RM ships 512x512 assets — bytes are opaque to GET.
MINIMAL_WEBP = (
    b"RIFF$\x00\x00\x00WEBPVP8 \x18\x00\x00\x00\xd0\x01\x00\x9d\x01*\x01\x00\x01\x00>"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)


def _staff_tree(tmp_path: Path, role_name: str = "cartographer", *, avatar_rel: str | None = None) -> Path:
    staff = tmp_path / "staff"
    roles_dir = staff / "roles"
    avatars_dir = staff / "avatars"
    roles_dir.mkdir(parents=True)
    avatars_dir.mkdir(parents=True)
    role = dict(_VALID_ROLE_DICT)
    role["name"] = role_name
    if avatar_rel:
        role["avatar"] = avatar_rel
        dest = avatars_dir / avatar_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(MINIMAL_WEBP)
    (roles_dir / f"{role_name}.yml").write_text(yaml.dump(role), encoding="utf-8")
    return roles_dir


@pytest.fixture
def avatar_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-avatar-test-token")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    roles_dir = _staff_tree(tmp_path, avatar_rel="cartographer.webp")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    monkeypatch.setenv("RD_AVATAR_DIR", str(tmp_path / "custom-avatars"))
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
    from server import app  # noqa: PLC0415

    auth = {"Authorization": "Bearer fleet-avatar-test-token"}
    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE, headers=auth)
    store_mod.reset_store()
    runner_mod.reset_runner()
    roles_mod.clear_roles_cache()


@pytest.mark.unit
def test_get_default_avatar_returns_bytes_etag_and_content_type(avatar_client: TestClient) -> None:
    res = avatar_client.get("/api/v1/staff/roles/cartographer/avatar")
    assert res.status_code == 200, res.text
    assert res.content == MINIMAL_WEBP
    assert res.headers["content-type"].startswith("image/webp")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "private, max-age=300"
    etag = res.headers["etag"].strip('"')
    assert len(etag) == 64


@pytest.mark.unit
def test_persona_subdirectory_avatar_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = _staff_tree(tmp_path, role_name="guest", avatar_rel="personas/marge.webp")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    from staff import roles as roles_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    spec = roles_mod.load_roles(roles_dir)["guest"]
    assert spec.valid is True
    assert spec.avatar == "personas/marge.webp"


@pytest.mark.unit
def test_role_without_avatar_returns_404_and_roster_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = _staff_tree(tmp_path, role_name="plain-role", avatar_rel=None)
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-avatar-test-token")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    from staff import roles as roles_mod  # noqa: PLC0415
    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    store_mod.reset_store()
    runner_mod.reset_runner()
    from server import app  # noqa: PLC0415

    client = TestClient(
        app,
        raise_server_exceptions=False,
        client=_REMOTE,
        headers={"Authorization": "Bearer fleet-avatar-test-token"},
    )
    assert client.get("/api/v1/staff/roles/plain-role/avatar").status_code == 404
    roster = client.get("/api/v1/staff/roster").json()
    row = next(r for r in roster["roles"] if r["name"] == "plain-role")
    assert row.get("avatar_source", "none") == "none"
    assert row.get("avatar_url") is None


@pytest.mark.unit
def test_traversal_role_id_is_404(avatar_client: TestClient) -> None:
    assert avatar_client.get("/api/v1/staff/roles/../etc/avatar").status_code == 404
    assert avatar_client.get("/api/v1/staff/roles/not-a-real-role/avatar").status_code == 404


@pytest.mark.unit
def test_yaml_avatar_path_traversal_marks_role_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staff = tmp_path / "staff"
    roles_dir = staff / "roles"
    (staff / "avatars").mkdir(parents=True)
    roles_dir.mkdir(parents=True)
    role = dict(_VALID_ROLE_DICT)
    role["name"] = "unsafe"
    role["avatar"] = "../../secrets.webp"
    (roles_dir / "unsafe.yml").write_text(yaml.dump(role), encoding="utf-8")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    from staff import roles as roles_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    spec = roles_mod.load_roles(roles_dir)["unsafe"]
    assert spec.valid is False
    assert any(".." in e or "avatar" in e for e in spec.errors)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-avatar-test-token")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    store_mod.reset_store()
    runner_mod.reset_runner()
    from server import app  # noqa: PLC0415

    client = TestClient(
        app,
        raise_server_exceptions=False,
        client=_REMOTE,
        headers={"Authorization": "Bearer fleet-avatar-test-token"},
    )
    assert client.get("/api/v1/staff/roles/unsafe/avatar").status_code == 404


@pytest.mark.unit
def test_agent_bearer_can_get_avatar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    id_dir = tmp_path / "identity"
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir(parents=True)
    token = "grok_avatar_read_test"  # pragma: allowlist secret
    (tok_dir / "agent-grok.token").write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    roles_dir = _staff_tree(tmp_path / "rm", avatar_rel="cartographer.webp")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    from identity import identity_manager  # noqa: PLC0415
    from staff import roles as roles_mod  # noqa: PLC0415
    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.load_principals()
    identity_manager.load_tokens()
    from agent_principal_defaults import merge_agent_principal_defaults  # noqa: PLC0415

    merge_agent_principal_defaults(identity_manager)
    roles_mod.clear_roles_cache()
    store_mod.reset_store()
    runner_mod.reset_runner()
    from server import app  # noqa: PLC0415

    client = TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    res = client.get(
        "/api/v1/staff/roles/cartographer/avatar",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert hashlib.sha256(res.content).hexdigest() == hashlib.sha256(MINIMAL_WEBP).hexdigest()


@pytest.mark.unit
def test_schema_accepts_avatar_and_rejects_unknown_key() -> None:
    from staff.validator import validate_role_data  # noqa: PLC0415

    role = dict(_VALID_ROLE_DICT)
    role["avatar"] = "cartographer.webp"
    assert not validate_role_data(role)
    role["bogus"] = True
    errs = validate_role_data(role)
    assert any("unknown field" in e for e in errs)
