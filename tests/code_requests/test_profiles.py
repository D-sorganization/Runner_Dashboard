"""Unit tests for Agent Profiles model and durable store (CR-3, Issue #1283)."""

from __future__ import annotations

from pathlib import Path

import pytest
from code_requests.profiles import AgentProfile, AgentProfileStore
from pydantic import ValidationError


def test_agent_profile_validation() -> None:
    """AgentProfile validates required fields, types, and constraints."""
    profile = AgentProfile(
        id="test-profile",
        name="Test Profile",
        provider="codex_cli",
        role="executor",
        standards=["tdd", "dbc"],
        budget={"max_cost": 2.5},
    )
    assert profile.id == "test-profile"
    assert profile.provider == "codex_cli"
    assert profile.role == "executor"
    assert "tdd" in profile.standards
    assert profile.budget["max_cost"] == 2.5

    # Invalid role should raise ValidationError
    with pytest.raises(ValidationError):
        AgentProfile(id="bad", name="Bad", provider="codex_cli", role="invalid_role")  # type: ignore[arg-type]


def test_agent_profile_store_seeds_defaults(tmp_path: Path) -> None:
    """Store automatically seeds default profiles when file does not exist."""
    store_path = tmp_path / "agent_profiles.json"
    assert not store_path.exists()

    store = AgentProfileStore(store_path)
    profiles = store.list()

    assert len(profiles) >= 2
    ids = {p.id for p in profiles}
    assert "planner-strong" in ids
    assert "executor-cli" in ids
    assert store_path.exists()


def test_agent_profile_store_crud(tmp_path: Path) -> None:
    """Store supports Create, Read, Update, Delete with persistence."""
    store_path = tmp_path / "agent_profiles.json"
    store = AgentProfileStore(store_path)

    # Create
    new_profile = AgentProfile(
        id="custom-planner",
        name="Custom Planner",
        provider="claude_code_cli",
        model="claude-3-7-sonnet",
        role="planner",
        standards=["tdd", "security"],
        is_default=False,
    )
    created = store.create(new_profile)
    assert created.id == "custom-planner"
    assert store.get("custom-planner") is not None

    # Read
    fetched = store.get("custom-planner")
    assert fetched is not None
    assert fetched.name == "Custom Planner"
    assert fetched.model == "claude-3-7-sonnet"

    # Update
    updated = store.update(
        "custom-planner",
        {"name": "Updated Custom Planner", "model": "claude-opus", "standards": ["tdd", "dbc", "dry"]},
    )
    assert updated is not None
    assert updated.name == "Updated Custom Planner"
    assert updated.model == "claude-opus"
    assert updated.standards == ["tdd", "dbc", "dry"]

    # Delete
    deleted = store.delete("custom-planner")
    assert deleted is True
    assert store.get("custom-planner") is None
    with pytest.raises(KeyError):
        store.delete("non-existent")


def test_agent_profile_default_switching(tmp_path: Path) -> None:
    """Marking a profile as default clears default flag on others with the same role."""
    store_path = tmp_path / "agent_profiles.json"
    store = AgentProfileStore(store_path)

    # Initial default planner
    p1 = AgentProfile(id="planner-1", name="P1", provider="claude_code_cli", role="planner", is_default=True)
    store.create(p1)
    assert store.get("planner-1") is not None
    assert store.get("planner-1").is_default is True

    # New default planner switches previous off
    p2 = AgentProfile(id="planner-2", name="P2", provider="gemini_cli", role="planner", is_default=True)
    store.create(p2)

    assert store.get("planner-2").is_default is True
    assert store.get("planner-1").is_default is False


def test_store_handles_corrupt_file(tmp_path: Path) -> None:
    """Store falls back to default profiles if JSON file is corrupt."""
    store_path = tmp_path / "corrupt_profiles.json"
    store_path.write_text("{not valid json", encoding="utf-8")

    store = AgentProfileStore(store_path)
    profiles = store.list()
    assert len(profiles) >= 2
