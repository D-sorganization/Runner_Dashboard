"""RM staff/avatars path contract for role YAML (#2680 slice 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.unit.test_staff_roles import _VALID_ROLE_DICT

MINIMAL_WEBP = (
    b"RIFF$\x00\x00\x00WEBPVP8 \x18\x00\x00\x00\xd0\x01\x00\x9d\x01*\x01\x00\x01\x00>"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)


def _staff_tree(tmp_path: Path, role_name: str, *, avatar_rel: str | None) -> Path:
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


@pytest.mark.unit
def test_persona_subdirectory_avatar_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = _staff_tree(tmp_path, "guest", avatar_rel="personas/marge.webp")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    from staff import roles as roles_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    spec = roles_mod.load_roles(roles_dir)["guest"]
    assert spec.valid is True
    assert spec.avatar == "personas/marge.webp"


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


@pytest.mark.unit
def test_missing_avatar_file_marks_role_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = _staff_tree(tmp_path, "ghost", avatar_rel=None)
    role_path = roles_dir / "ghost.yml"
    data = yaml.safe_load(role_path.read_text(encoding="utf-8"))
    data["avatar"] = "missing.webp"
    role_path.write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    from staff import roles as roles_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    spec = roles_mod.load_roles(roles_dir)["ghost"]
    assert spec.valid is False
    assert any("not found" in e for e in spec.errors)


@pytest.mark.unit
def test_load_roles_uses_avatars_from_loaded_directory_not_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``load_roles(directory)`` must validate avatars under that tree (Codex P2)."""
    tree_a = _staff_tree(tmp_path / "a", "guest", avatar_rel=None)
    role_path = tree_a / "guest.yml"
    data = yaml.safe_load(role_path.read_text(encoding="utf-8"))
    data["avatar"] = "only-in-b.webp"
    role_path.write_text(yaml.dump(data), encoding="utf-8")

    tree_b = _staff_tree(tmp_path / "b", "guest", avatar_rel="only-in-b.webp")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tree_a))
    from staff import roles as roles_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    assert roles_mod.load_roles(tree_a)["guest"].valid is False
    roles_mod.clear_roles_cache()
    assert roles_mod.load_roles(tree_b)["guest"].valid is True


@pytest.mark.unit
def test_avatar_file_added_without_yaml_change_refreshes_validation(tmp_path: Path) -> None:
    roles_dir = _staff_tree(tmp_path, "ghost", avatar_rel=None)
    role_path = roles_dir / "ghost.yml"
    data = yaml.safe_load(role_path.read_text(encoding="utf-8"))
    data["avatar"] = "appears.webp"
    role_path.write_text(yaml.dump(data), encoding="utf-8")
    from staff import roles as roles_mod  # noqa: PLC0415

    roles_mod.clear_roles_cache()
    assert roles_mod.load_roles(roles_dir)["ghost"].valid is False
    (roles_dir.parent / "avatars" / "appears.webp").write_bytes(MINIMAL_WEBP)
    assert roles_mod.load_roles(roles_dir)["ghost"].valid is True


@pytest.mark.unit
def test_schema_accepts_avatar_and_rejects_unknown_key() -> None:
    from staff.validator import validate_role_data  # noqa: PLC0415

    role = dict(_VALID_ROLE_DICT)
    role["avatar"] = "cartographer.webp"
    assert not validate_role_data(role)
    role["bogus"] = True
    errs = validate_role_data(role)
    assert any("unknown field" in e for e in errs)
