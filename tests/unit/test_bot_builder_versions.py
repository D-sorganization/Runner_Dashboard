"""Immutable version/rollback/pinned-task-version contract tests (RD #3104)."""

from __future__ import annotations

import pytest
from staff.bot_builder import parse_builder_role
from staff.bot_builder.versions import RoleVersionLedger, compute_content_hash, dispatch_task


def _role(persona: str = "Alpha persona.") -> object:
    return parse_builder_role(
        {
            "schema_version": 1,
            "name": "pilot-bot",
            "persona": persona,
            "instructions": "Answer briefly.",
            "skills": [],
            "model": None,
            "providers": ["sub-cli-alpha"],
            "schedule": None,
            "triggers": [],
            "host": "grokbot-first",
            "enabled": False,
            "capabilities": ["rd.staff_api"],
            "private_context_ref": None,
            "household_data_opt_in": False,
        },
    )


def test_content_hash_deterministic_and_content_sensitive() -> None:
    assert compute_content_hash(_role()) == compute_content_hash(_role())
    assert compute_content_hash(_role("Alpha persona.")) != compute_content_hash(_role("Beta persona."))


def test_publish_builds_immutable_history() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1 = ledger.publish(_role("Alpha persona."))
    v2 = ledger.publish(_role("Beta persona."))
    assert ledger.history == (v1, v2)
    assert ledger.current.version_id == v2.version_id
    with pytest.raises(AttributeError):
        v1.version_id = "tampered"  # type: ignore[misc]


def test_rollback_creates_new_version_and_preserves_history() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1 = ledger.publish(_role("Alpha persona."))
    ledger.publish(_role("Beta persona."))
    v3 = ledger.rollback(v1.version_id)

    assert v3.version_id != v1.version_id
    assert v3.content_hash == v1.content_hash
    assert v3.rolled_back_from == v1.version_id
    assert len(ledger.history) == 3
    assert ledger.history[0] is v1

    with pytest.raises(KeyError):
        ledger.rollback("no-such-version")


def test_pinned_version_unaffected_by_publish_mid_task() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    ledger.publish(_role("Alpha persona."))

    task = dispatch_task(ledger)
    assert task.pinned_version_id == ledger.current.version_id

    ledger.publish(_role("Beta persona."))

    assert task.pinned_version_id != ledger.current.version_id
    assert task.pinned_role.persona == "Alpha persona."
