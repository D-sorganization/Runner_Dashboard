"""Form-first bot-builder UI flow, approval screen, fleet list, and history tests (RD #3106)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import orchestration_audit as _audit
import pytest
from staff.bot_builder import BUILDER_EDITOR_V1, DraftMode, GrantCeiling, RoleVersionLedger, ShadowDryRunSession
from staff.bot_builder.ui import (
    DEFAULT_BUILDER_TEMPLATES,
    BuilderFormValues,
    BuilderUiError,
    BuilderWorkspaceController,
    open_editor_view,
    render_fleet_bot_list,
    render_version_history,
    review_dry_run,
)

CEILING_FILE = Path(__file__).resolve().parents[2] / "config" / "bot_builder_grant_ceiling.json"
_CEILING = GrantCeiling(
    approved_providers=frozenset({"sub-cli-alpha", "sub-cli-beta"}),
    allowed_capabilities=frozenset({"rd.staff_api", "rd.cli_sessions"}),
    household_capabilities=frozenset({"household.mail"}),
)


def _form(**overrides: object) -> BuilderFormValues:
    base = BuilderFormValues(
        "pilot-bot", "Alpha persona.", "Answer briefly.", ("status-summary",), "routine-default", ("sub-cli-alpha",)
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "orchestration_audit.ndjson"
    monkeypatch.setattr(_audit, "ORCHESTRATION_AUDIT_PATH", path)
    return path


def test_v1_builder_ui_is_form_only_and_rejects_yaml_or_advanced_mode() -> None:
    assert open_editor_view("form") == BUILDER_EDITOR_V1
    for unsupported in ("yaml", "advanced", "raw"):
        with pytest.raises(BuilderUiError, match="form-only"):
            open_editor_view(unsupported)


@pytest.mark.asyncio
async def test_all_four_creation_modes_flow_through_draft_validate_preview_publish() -> None:
    ledgers: dict[str, RoleVersionLedger] = {}
    ctrl = BuilderWorkspaceController(ceiling=_CEILING, ledgers=ledgers)

    tpl_form = BuilderFormValues.from_template(DEFAULT_BUILDER_TEMPLATES[0], name="tpl-bot")
    ctrl.start_draft(DraftMode.TEMPLATE, tpl_form)
    assert ctrl.validate_current().ok is True and ctrl.preview_current().validation_ok is True
    v_tpl = await ctrl.publish_current(principal="dieter")
    assert v_tpl.role.name == "tpl-bot" and v_tpl.role.enabled is False

    ctrl.start_draft(DraftMode.SCRATCH, _form(name="scratch-bot"))
    assert ctrl.validate_current().ok is True and ctrl.preview_current().validation_ok is True
    v_scratch = await ctrl.publish_current(principal="dieter")

    clone_form = BuilderFormValues.from_version(v_scratch, name="clone-bot")
    ctrl.start_draft(DraftMode.CLONE, clone_form, source=v_scratch)
    assert ctrl.validate_current().ok is True and ctrl.preview_current().validation_ok is True
    await ctrl.publish_current(principal="dieter")

    edit_form = BuilderFormValues.from_version(v_scratch, description="Updated summary only.")
    ctrl.start_draft(DraftMode.EDIT, edit_form, role_id="scratch-bot")
    assert ctrl.validate_current().ok is True and ctrl.preview_current().requires_standalone_approval is False
    v_edit = await ctrl.publish_current(principal="dieter")
    assert v_edit.sequence == 2

    fleet = {item.role_id: item for item in render_fleet_bot_list(ledgers, leaders={"scratch-bot": "grokbot"})}
    assert set(fleet) == {"tpl-bot", "scratch-bot", "clone-bot"}
    assert (fleet["scratch-bot"].current_version_id, fleet["scratch-bot"].leader, fleet["scratch-bot"].state) == (
        v_edit.version_id,
        "grokbot",
        "off",
    )


@pytest.mark.asyncio
async def test_standalone_approval_screen_renders_cumulative_and_version_diffs_and_blocks_until_confirmed() -> None:
    ledgers: dict[str, RoleVersionLedger] = {}
    ctrl = BuilderWorkspaceController(ceiling=_CEILING, ledgers=ledgers)
    ctrl.start_draft(DraftMode.SCRATCH, _form())
    await ctrl.publish_current(principal="dieter")

    ctrl.start_draft(DraftMode.EDIT, _form(persona="Beta persona."), role_id="pilot-bot")
    assert ctrl.preview_current().requires_standalone_approval is True
    with pytest.raises(BuilderUiError, match="standalone approval"):
        await ctrl.publish_current(principal="dieter")

    screen_v2 = ctrl.open_approval_screen()
    assert "persona" in screen_v2.this_version_diff and "persona" in screen_v2.plain_english_summary.lower()
    ctrl.confirm_approval()
    await ctrl.publish_current(principal="dieter")

    widened = _form(persona="Beta persona.", capabilities=("rd.staff_api", "rd.cli_sessions"))
    ctrl.start_draft(DraftMode.EDIT, widened, role_id="pilot-bot")
    screen_v3 = ctrl.open_approval_screen()
    assert "rd.cli_sessions" in screen_v3.this_version_diff
    assert "rd.cli_sessions" in screen_v3.cumulative_diff_vs_original
    assert "rd.cli_sessions" in screen_v3.plain_english_summary
    ctrl.confirm_approval()
    v3 = await ctrl.publish_current(principal="dieter")
    assert v3.sequence == 3

    bundled = _form(persona="Gamma persona.", description="Bundled unrelated description edit.")
    ctrl.start_draft(DraftMode.EDIT, bundled, role_id="pilot-bot")
    assert ctrl.open_approval_screen().bundling_violation is True
    with pytest.raises(BuilderUiError, match="bundled"):
        ctrl.confirm_approval()


@pytest.mark.asyncio
async def test_version_history_rollback_dry_run_review_and_activation_flow(_audit_file: Path) -> None:
    ledgers: dict[str, RoleVersionLedger] = {}
    ctrl = BuilderWorkspaceController(ceiling=_CEILING, ledgers=ledgers)

    ctrl.start_draft(DraftMode.SCRATCH, _form())
    v1 = await ctrl.publish_current(principal="dieter")
    ctrl.start_draft(DraftMode.EDIT, _form(persona="Beta persona."), role_id="pilot-bot")
    ctrl.open_approval_screen()
    ctrl.confirm_approval()
    v2 = await ctrl.publish_current(principal="dieter")

    history = render_version_history(ledgers["pilot-bot"])
    assert [h.version_id for h in history] == [v1.version_id, v2.version_id]
    assert history[0].can_rollback is True and history[1].is_current is True

    v3 = await ctrl.rollback_role("pilot-bot", v1.version_id, principal="dieter")
    assert v3.rolled_back_from == v1.version_id and v3.content_hash == v1.content_hash

    session = ShadowDryRunSession(ledgers["pilot-bot"])
    session.board_post("Dry-run status post", target="RM#2376")
    dry_run = session.finish()
    review = review_dry_run(ledgers["pilot-bot"], dry_run)
    assert review.activate_button_enabled is True and review.all_mutations_refused is True

    await ctrl.activate_role("pilot-bot", dry_run, approver="dieter", notes="Dry run verified.")
    assert render_fleet_bot_list(ledgers)[0].state == "on"

    with pytest.raises(BuilderUiError, match="hold"):
        ctrl.reject_hold_or_ceiling_edit({"holds": ["rd-2657"]})
    with pytest.raises(BuilderUiError, match="ceiling"):
        ctrl.reject_hold_or_ceiling_edit({"allowed_capabilities": ["rd.runners"]})

    before = CEILING_FILE.read_bytes()
    with pytest.raises(dataclasses.FrozenInstanceError):
        _CEILING.allowed_capabilities = frozenset()  # type: ignore[misc]
    assert CEILING_FILE.read_bytes() == before
    entries = [json.loads(line) for line in _audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {e.get("action") for e in entries} >= {"bot_builder.publish", "bot_builder.rollback", "bot_builder.activate"}
