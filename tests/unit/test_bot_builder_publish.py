"""Draft/validate/preview/publish API contract tests (RD #3104, slice 10)."""

from __future__ import annotations

import json
from pathlib import Path

import orchestration_audit as _audit
import pytest
from staff.bot_builder.grant_ceiling import GrantCeiling
from staff.bot_builder.publish import (
    DraftMode,
    PublishError,
    create_draft,
    preview_draft,
    publish_draft,
    rollback_publish,
    validate_draft,
)
from staff.bot_builder.versions import RoleVersionLedger

_CEILING = GrantCeiling(
    approved_providers=frozenset({"sub-cli-alpha"}),
    allowed_capabilities=frozenset({"rd.staff_api", "rd.cli_sessions"}),
    household_capabilities=frozenset({"household.mail"}),
)
_TIGHT_CEILING = GrantCeiling(
    approved_providers=frozenset({"sub-cli-alpha"}),
    allowed_capabilities=frozenset({"rd.staff_api"}),
    household_capabilities=frozenset({"household.mail"}),
)


def _data(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "name": "pilot-bot",
        "persona": "Alpha persona.",
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
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "orchestration_audit.ndjson"
    monkeypatch.setattr(_audit, "ORCHESTRATION_AUDIT_PATH", path)
    return path


def _read_audit(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- Creation-mode tests (acceptance: API tests for each creation mode) ---


@pytest.mark.parametrize("mode", [DraftMode.SCRATCH, DraftMode.TEMPLATE])
def test_draft_from_scratch_and_template(mode: DraftMode) -> None:
    draft = create_draft(mode, _data())
    assert draft.mode is mode
    assert validate_draft(draft, _CEILING).ok


def test_draft_from_clone_revalidates_and_fails_under_tightened_ceiling() -> None:
    ledger = RoleVersionLedger(role_id="source-bot")
    source_version = ledger.publish(
        create_draft(DraftMode.SCRATCH, _data(capabilities=["rd.staff_api", "rd.cli_sessions"])).role,
    )
    # Clone was valid under the looser ceiling.
    clone_draft = create_draft(
        DraftMode.CLONE,
        _data(name="clone-bot", capabilities=["rd.staff_api", "rd.cli_sessions"]),
        source=source_version,
    )
    assert clone_draft.mode is DraftMode.CLONE
    assert validate_draft(clone_draft, _CEILING).ok

    # Same clone fails fresh validation once the ceiling tightens - clone
    # mode never inherits the source's prior pass.
    assert not validate_draft(clone_draft, _TIGHT_CEILING).ok


# --- Preview: exact diff, policy findings, host/export effects ---


def test_preview_fresh_role_has_no_diff_but_has_effects() -> None:
    draft = create_draft(DraftMode.SCRATCH, _data())
    preview = preview_draft(draft, _CEILING)
    assert preview.validation.ok
    assert preview.host_effects["host"] == "grokbot-first"
    assert "rd.staff_api" in preview.export_effects["capabilities"]
    assert preview.diff is None


def test_preview_exact_diff_on_edit() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    ledger.publish(create_draft(DraftMode.SCRATCH, _data()).role)
    draft = create_draft(DraftMode.EDIT, _data(persona="Beta persona."), ledger=ledger)
    preview = preview_draft(draft, _CEILING)
    assert preview.diff is not None
    assert "persona" in preview.diff.requires_confirmation


# --- Publish / confirmation gating ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edited_data",
    [
        _data(persona="Beta persona."),
        _data(capabilities=["rd.staff_api", "rd.cli_sessions"]),
    ],
)
async def test_publish_blocked_without_confirmation(edited_data: dict[str, object]) -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    ledger.publish(create_draft(DraftMode.SCRATCH, _data()).role)
    draft = create_draft(DraftMode.EDIT, edited_data, ledger=ledger)
    with pytest.raises(PublishError, match="confirmation"):
        await publish_draft(draft, _CEILING, ledger, confirmed=False, principal="dieter")


@pytest.mark.asyncio
async def test_publish_succeeds_with_confirmation_and_writes_audit(_audit_file: Path) -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    ledger.publish(create_draft(DraftMode.SCRATCH, _data()).role)
    draft = create_draft(DraftMode.EDIT, _data(persona="Beta persona."), ledger=ledger)
    version = await publish_draft(draft, _CEILING, ledger, confirmed=True, principal="dieter")
    assert ledger.current.version_id == version.version_id
    entries = _read_audit(_audit_file)
    assert any(e.get("action") == "bot_builder.publish" for e in entries)


@pytest.mark.asyncio
async def test_publish_fresh_scratch_role_needs_no_confirmation() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    draft = create_draft(DraftMode.SCRATCH, _data())
    version = await publish_draft(draft, _CEILING, ledger, confirmed=False, principal="dieter")
    assert ledger.current.version_id == version.version_id


@pytest.mark.asyncio
async def test_publish_rejects_invalid_role() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    draft = create_draft(DraftMode.SCRATCH, _data(providers=["pay-per-use-api"]))
    with pytest.raises(PublishError, match="validation"):
        await publish_draft(draft, _CEILING, ledger, confirmed=True, principal="dieter")


# --- Rollback ---


@pytest.mark.asyncio
async def test_rollback_publishes_new_version_equal_to_prior_and_logs_audit(_audit_file: Path) -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1 = ledger.publish(create_draft(DraftMode.SCRATCH, _data()).role)
    draft = create_draft(DraftMode.EDIT, _data(persona="Beta persona."), ledger=ledger)
    await publish_draft(draft, _CEILING, ledger, confirmed=True, principal="dieter")

    rolled_back = await rollback_publish(ledger, v1.version_id, _CEILING, principal="dieter")
    assert rolled_back.content_hash == v1.content_hash
    assert rolled_back.version_id != v1.version_id

    entries = _read_audit(_audit_file)
    assert any(e.get("action") == "bot_builder.rollback" for e in entries)
