"""Unit tests for staff role registry, schema validation, and mtime caching (#1300)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from staff import roles as roles_mod
from staff.rm_sync import source_status
from staff.runner import RunRequest, StaffRunner
from staff.store import RunStore


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    roles_mod.clear_roles_cache()
    yield
    roles_mod.clear_roles_cache()


_VALID_ROLE_DICT: dict[str, Any] = {
    "name": "cartographer",
    "title": "Cartographer - Fleet Map & Dependency Auditor",
    "summary": "Maintains the architecture maps, cross-repository dependency graphs, and contract seams.",
    "playbook": "docs/fleet-cartographer.md",
    "prompt_template": "docs/templates/cartographer-report.md",
    "instructions": "Audit fleet dependencies and enforce architecture map freshness.",
    "providers": ["claude", "antigravity"],
    "model": "default",
    "schedule": "0 2 * * 1",
    "window": {"start": "01:00", "end": "05:00"},
    "repos": ["Runner_Dashboard", "Repository_Management"],
    "scope": {
        "audits": ["architecture-maps", "contract-seams"],
        "owns": ["docs/architecture/"],
    },
    "budget": {"usd_per_run": 2.0, "usd_per_day": 10.0},
    "permissions": {
        "lease": True,
        "push_branch": True,
        "open_pr": True,
        "merge": False,
        "host_shell": False,
        "notify_user": False,
    },
    "reports_to": "orchestrator",
    "holds": ["no manual architecture doc drift"],
    "surface": "dashboard",
    "persona": "Detail-oriented mapmaker and systems architect.",
    "group": "Architecture",
    "chat": {
        "providers": ["claude"],
        "read_only_tools": ["view_file", "search_code"],
    },
}


@pytest.mark.unit
def test_parse_role_loads_full_schema_and_chat_fields() -> None:
    spec = roles_mod.parse_role(_VALID_ROLE_DICT, source_path="/path/cartographer.yml")
    assert spec.name == "cartographer"
    assert spec.title == "Cartographer - Fleet Map & Dependency Auditor"
    assert spec.prompt_template == "docs/templates/cartographer-report.md"
    assert spec.scope == {
        "audits": ["architecture-maps", "contract-seams"],
        "owns": ["docs/architecture/"],
    }
    assert spec.persona == "Detail-oriented mapmaker and systems architect."
    assert spec.group == "Architecture"
    assert spec.chat == {
        "providers": ["claude"],
        "read_only_tools": ["view_file", "search_code"],
    }
    assert spec.valid is True
    assert spec.errors == ()
    assert spec.dispatchable is True

    d = spec.to_dict()
    assert d["prompt_template"] == "docs/templates/cartographer-report.md"
    assert d["scope"] == spec.scope
    assert d["persona"] == spec.persona
    assert d["group"] == "Architecture"
    assert d["chat"] == spec.chat
    assert d["valid"] is True
    assert d["errors"] == []
    assert d["error"] is None


@pytest.mark.unit
def test_valid_role_file_passes_schema_validation(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    role_file = roles_dir / "cartographer.yml"
    role_file.write_text(yaml.dump(_VALID_ROLE_DICT), encoding="utf-8")

    roles = roles_mod.load_roles(roles_dir)
    assert "cartographer" in roles
    spec = roles["cartographer"]
    assert spec.valid is True
    assert spec.errors == ()
    assert spec.dispatchable is True


@pytest.mark.unit
def test_role_file_with_schema_error_surfaced_as_invalid(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    # Missing required 'instructions', bad schedule cron, negative budget
    bad_role = dict(_VALID_ROLE_DICT)
    bad_role["name"] = "bad-role"
    del bad_role["instructions"]
    bad_role["schedule"] = "not a valid cron"
    bad_role["budget"] = {"usd_per_run": -5.0, "usd_per_day": 10.0}

    (roles_dir / "bad-role.yml").write_text(yaml.dump(bad_role), encoding="utf-8")

    roles = roles_mod.load_roles(roles_dir)
    assert "bad-role" in roles
    spec = roles["bad-role"]
    assert spec.valid is False
    assert len(spec.errors) > 0
    # Error message should mention missing instructions or invalid schedule
    combined = " ".join(spec.errors)
    assert "instructions" in combined or "schedule" in combined or "budget" in combined


@pytest.mark.unit
def test_corrupt_yaml_file_surfaced_as_invalid(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "broken.yml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    (roles_dir / "syntax_err.yml").write_text("name: [unclosed bracket", encoding="utf-8")

    roles = roles_mod.load_roles(roles_dir)
    assert "broken" in roles
    assert roles["broken"].valid is False
    assert roles["broken"].dispatchable is False
    assert any("mapping" in err.lower() for err in roles["broken"].errors)

    assert "syntax_err" in roles
    assert roles["syntax_err"].valid is False
    assert roles["syntax_err"].dispatchable is False
    assert len(roles["syntax_err"].errors) > 0


@pytest.mark.unit
def test_mtime_cache_reads_once_per_change(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    f1 = roles_dir / "cartographer.yml"
    f1.write_text(yaml.dump(_VALID_ROLE_DICT), encoding="utf-8")

    # Initial load reads files
    roles1 = roles_mod.load_roles(roles_dir)
    assert "cartographer" in roles1

    # Second load: without modifying file, read_text should NOT be called
    with patch.object(Path, "read_text", side_effect=AssertionError("Should not read unchanged file")):
        roles2 = roles_mod.load_roles(roles_dir)
        assert roles2["cartographer"].name == "cartographer"

    # Modify file: update mtime to future
    new_mtime = f1.stat().st_mtime_ns + 10_000_000_000
    mod_data = dict(_VALID_ROLE_DICT)
    mod_data["title"] = "Updated Cartographer"
    f1.write_text(yaml.dump(mod_data), encoding="utf-8")
    import os

    os.utime(f1, ns=(new_mtime, new_mtime))

    roles3 = roles_mod.load_roles(roles_dir)
    assert roles3["cartographer"].title == "Updated Cartographer"


@pytest.mark.unit
def test_role_validation_errors_and_source_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "cartographer.yml").write_text(yaml.dump(_VALID_ROLE_DICT), encoding="utf-8")
    (roles_dir / "broken.yml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path))

    errors = roles_mod.role_validation_errors(roles_dir)
    assert "broken.yml" in errors
    assert "cartographer.yml" not in errors

    status = source_status(tmp_path, state_path=tmp_path / "state.json")
    assert "validation_errors" in status
    assert "broken.yml" in status["validation_errors"]


@pytest.mark.unit
def test_invalid_role_cannot_be_dispatched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "broken.yml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    store = RunStore(tmp_path / "runs.sqlite3")
    runner = StaffRunner(store=store, roles_loader=lambda: roles_mod.load_roles(roles_dir))

    req = RunRequest(role="broken", prompt="test")
    with pytest.raises(ValueError, match="not dispatchable"):
        runner.plan(req)


@pytest.mark.unit
def test_parse_role_with_fleet_actions_and_approvals() -> None:
    role_dict = dict(_VALID_ROLE_DICT)
    role_dict["permissions"] = dict(_VALID_ROLE_DICT["permissions"])
    role_dict["permissions"]["fleet_actions"] = ["runner.start", "runner.stop"]
    role_dict["permissions"]["approvals"] = {"runner.start": "confirm", "runner.stop": "owner"}

    spec = roles_mod.parse_role(role_dict, source_path="/path/test.yml")
    assert spec.fleet_actions == ("runner.start", "runner.stop")
    assert spec.approvals == {"runner.start": "confirm", "runner.stop": "owner"}

    d = spec.to_dict()
    assert d["fleet_actions"] == ["runner.start", "runner.stop"]
    assert d["approvals"] == {"runner.start": "confirm", "runner.stop": "owner"}


@pytest.mark.unit
def test_validate_role_fleet_actions_and_approvals(tmp_path: Path) -> None:
    from staff.validator import validate_role_data

    role_dict = dict(_VALID_ROLE_DICT)
    role_dict["permissions"] = dict(_VALID_ROLE_DICT["permissions"])
    role_dict["permissions"]["fleet_actions"] = ["queue.diagnose", "runner.start"]
    role_dict["permissions"]["approvals"] = {
        "queue.diagnose": "auto",
        "runner.start": "confirm",
    }
    assert validate_role_data(role_dict) == []

    # Unknown action fails
    bad_action = dict(role_dict)
    bad_action["permissions"] = dict(role_dict["permissions"])
    bad_action["permissions"]["fleet_actions"] = ["runner.burn_down"]
    problems = validate_role_data(bad_action)
    assert any("unknown fleet action" in p for p in problems)

    # Loosening default approval fails (e.g. runner.stop default confirm -> auto)
    loosen = dict(role_dict)
    loosen["permissions"] = dict(role_dict["permissions"])
    loosen["permissions"]["fleet_actions"] = ["runner.stop"]
    loosen["permissions"]["approvals"] = {"runner.stop": "auto"}
    problems_loosen = validate_role_data(loosen)
    assert any("cannot loosen default policy" in p for p in problems_loosen)

    # Approvals without fleet actions fails
    no_actions = dict(role_dict)
    no_actions["permissions"] = dict(role_dict["permissions"])
    no_actions["permissions"].pop("fleet_actions", None)
    no_actions["permissions"]["approvals"] = {"runner.start": "confirm"}
    problems_no_actions = validate_role_data(no_actions)
    assert any("approvals declared without fleet_actions" in p for p in problems_no_actions)
