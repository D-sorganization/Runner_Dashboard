"""Validates the queue-shepherd staff role file against the fleet role schema (#2929 slice 3).

This role's whole point is advisory-only merge-queue triage: it must never carry
a permission that lets it mutate the queue, and its prompt must say so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from staff.role_spec import parse_role
from staff.validator import validate_role_data

ROLE_PATH = Path(__file__).resolve().parents[1] / "docs" / "agents" / "role-drafts" / "queue-shepherd.yml"


@pytest.fixture
def role_data() -> dict[str, Any]:
    return yaml.safe_load(ROLE_PATH.read_text(encoding="utf-8"))


def test_role_file_exists_and_is_a_mapping(role_data: dict[str, Any]) -> None:
    assert isinstance(role_data, dict)
    assert role_data["name"] == "queue-shepherd"


def test_role_passes_the_fleet_schema(role_data: dict[str, Any]) -> None:
    errors = validate_role_data(role_data)
    assert errors == []


def test_role_parses_to_a_valid_spec(role_data: dict[str, Any]) -> None:
    spec = parse_role(role_data, str(ROLE_PATH))

    assert spec.valid, spec.errors
    assert spec.name == "queue-shepherd"
    assert spec.surface == "dashboard"


def test_role_permissions_are_strictly_read_only(role_data: dict[str, Any]) -> None:
    spec = parse_role(role_data, str(ROLE_PATH))

    # No permission lets this role mutate the queue, push code, or merge.
    assert spec.permissions["lease"] is False
    assert spec.permissions["push_branch"] is False
    assert spec.permissions["open_pr"] is False
    assert spec.permissions["merge"] is False
    assert spec.permissions["host_shell"] is False
    assert spec.code_read_only is True
    assert spec.fleet_actions == ()


def test_role_prompt_and_holds_state_dry_run_only(role_data: dict[str, Any]) -> None:
    spec = parse_role(role_data, str(ROLE_PATH))

    instructions = spec.instructions.lower()
    assert "dry-run" in instructions or "dry run" in instructions
    assert "never" in instructions

    holds_text = " ".join(spec.holds).lower()
    assert "never" in holds_text
    assert "reorder" in holds_text or "cancel" in holds_text
