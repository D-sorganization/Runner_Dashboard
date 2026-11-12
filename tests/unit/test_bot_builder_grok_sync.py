"""Unit tests for #3108 (bot-builder slice 14): Grok Bot sync via #3099 bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orchestration_audit as _audit
import pytest
from staff.bot_builder.grant_ceiling import GrantCeiling
from staff.bot_builder.grok_sync import (
    DEFAULT_GROK_SYNC_STALENESS_TTL_S,
    GrokBotReadOnlyError,
    GrokBotSyncState,
    GrokSyncDriftError,
    build_builder_version_bundle,
    evaluate_grok_sync_status,
    export_published_version_bundle,
    export_rollback_version_bundle,
    refuse_grok_role_config_edit,
    verify_grok_bundle_import,
)
from staff.bot_builder.publish import DraftMode, create_draft, publish_draft, rollback_publish
from staff.bot_builder.versions import RoleVersionLedger
from staff.grok_role_bundle import BUNDLE_FORMAT, assert_bundle_export_safe, canonical_payload_hash

_CEILING = GrantCeiling(
    approved_providers=frozenset({"sub-cli-alpha"}),
    allowed_capabilities=frozenset({"rd.staff_api", "rd.cli_sessions"}),
    household_capabilities=frozenset({"household.mail"}),
)


def _role_data(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "pilot-bot",
        "persona": "Alpha persona.",
        "instructions": "Answer briefly.",
        "skills": ["triage"],
        "model": "claude-opus-5-5",
        "providers": ["sub-cli-alpha"],
        "schedule": "0 9 * * 1-5",
        "triggers": [{"target_role": "reviewer-bot", "event": "pr.opened"}],
        "host": "grokbot-first",
        "enabled": False,
        "capabilities": ["rd.staff_api"],
        "private_context_ref": "pilot-bot/work-store",
        "household_data_opt_in": False,
        "description": "Pilot builder bot for Grok sync.",
        **overrides,
    }


@pytest.fixture(autouse=True)
def _audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "orchestration_audit.ndjson"
    monkeypatch.setattr(_audit, "ORCHESTRATION_AUDIT_PATH", path)
    return path


@pytest.mark.asyncio
async def test_export_on_publish_and_rollback_reexports_restored_content(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundles"
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1 = await publish_draft(
        create_draft(DraftMode.SCRATCH, _role_data()), _CEILING, ledger, confirmed=False, principal="dieter"
    )
    export_v1 = export_published_version_bundle(v1, bundle_root, synced_at=1000.0)
    assert (export_v1.role, export_v1.version_id, export_v1.source_content_hash) == (
        "pilot-bot",
        v1.version_id,
        v1.content_hash,
    )
    assert export_v1.payload["bundle_format"] == BUNDLE_FORMAT
    assert export_v1.payload_hash == canonical_payload_hash(export_v1.payload)
    assert_bundle_export_safe(export_v1.payload)
    assert "private_context_ref" not in export_v1.payload and "work-store" not in str(export_v1.payload)

    v2 = await publish_draft(
        create_draft(DraftMode.EDIT, _role_data(persona="Beta persona."), ledger=ledger),
        _CEILING,
        ledger,
        confirmed=True,
        principal="dieter",
    )
    export_v2 = export_published_version_bundle(v2, bundle_root, synced_at=1100.0)
    assert export_v2.payload_hash != export_v1.payload_hash and export_v2.payload["persona"] == "Beta persona."

    rolled_back = await rollback_publish(ledger, v1.version_id, _CEILING, principal="dieter")
    export_rb = export_rollback_version_bundle(rolled_back, bundle_root, synced_at=1200.0)
    assert (export_rb.version_id, export_rb.source_content_hash, export_rb.payload_hash, export_rb.payload) == (
        rolled_back.version_id,
        v1.content_hash,
        export_v1.payload_hash,
        export_v1.payload,
    )


@pytest.mark.asyncio
async def test_mismatched_hash_fails_closed_raises_notice_and_never_overwrites(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundles"
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1 = await publish_draft(
        create_draft(DraftMode.SCRATCH, _role_data()), _CEILING, ledger, confirmed=False, principal="dieter"
    )
    exported = export_published_version_bundle(v1, bundle_root, synced_at=1000.0)
    role_json_path = bundle_root / "roles" / "pilot-bot.json"
    before_role_bytes = role_json_path.read_bytes()
    sent_notices: list[tuple[str, dict[str, Any]]] = []
    bad_hash = "f" * 64

    with pytest.raises(GrokSyncDriftError, match="hash mismatch"):
        verify_grok_bundle_import(
            bundle_root,
            role="pilot-bot",
            reported_payload_hash=bad_hash,
            now=1050.0,
            notifier=lambda r, m: sent_notices.append((r, dict(m))) or {"ok": True, "status": "sent"},
        )

    assert role_json_path.read_bytes() == before_role_bytes and len(sent_notices) == 1
    notified_role, notice = sent_notices[0]
    assert (notified_role, notice["kind"]) == ("rd-barb", "alert")
    assert "pilot-bot" in notice["summary"] and exported.payload_hash[:12] in notice["summary"]
    assert "recommended_action" in notice

    status = evaluate_grok_sync_status(bundle_root, role="pilot-bot", now=1060.0)
    assert (status.state, status.grok_bot_enabled, status.expected_payload_hash, status.reported_payload_hash) == (
        GrokBotSyncState.DRIFT_BLOCKED,
        False,
        exported.payload_hash,
        bad_hash,
    )


@pytest.mark.asyncio
async def test_ttl_staleness_marks_unsynced_grok_bot_disabled_and_grok_read_only(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundles"
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1 = await publish_draft(
        create_draft(DraftMode.SCRATCH, _role_data()), _CEILING, ledger, confirmed=False, principal="dieter"
    )
    exported = export_published_version_bundle(v1, bundle_root, synced_at=1000.0)
    verify_grok_bundle_import(bundle_root, role="pilot-bot", reported_payload_hash=exported.payload_hash, now=1010.0)
    in_sync = evaluate_grok_sync_status(bundle_root, role="pilot-bot", now=1020.0)
    assert (in_sync.state, in_sync.grok_bot_enabled) == (GrokBotSyncState.IN_SYNC, True)

    v2 = await publish_draft(
        create_draft(DraftMode.EDIT, _role_data(persona="Updated persona."), ledger=ledger),
        _CEILING,
        ledger,
        confirmed=True,
        principal="dieter",
    )
    export_published_version_bundle(v2, bundle_root, synced_at=2000.0)
    pending = evaluate_grok_sync_status(
        bundle_root, role="pilot-bot", now=2000.0 + DEFAULT_GROK_SYNC_STALENESS_TTL_S - 1.0
    )
    assert (pending.state, pending.grok_bot_enabled) == (GrokBotSyncState.PENDING_SYNC, False)

    notices: list[dict[str, Any]] = []
    stale = evaluate_grok_sync_status(
        bundle_root,
        role="pilot-bot",
        now=2000.0 + DEFAULT_GROK_SYNC_STALENESS_TTL_S + 1.0,
        notifier=lambda _r, m: notices.append(dict(m)) or {"ok": True, "status": "sent"},
    )
    assert (stale.state, stale.grok_bot_enabled) == (GrokBotSyncState.STALE_DISABLED, False)
    assert "stale" in stale.reason.lower() and len(notices) == 1 and notices[0]["kind"] == "alert"

    with pytest.raises(GrokBotReadOnlyError, match="read-only"):
        refuse_grok_role_config_edit("pilot-bot", attempted_fields=("persona", "instructions"))
    assert build_builder_version_bundle(v1).payload["grok_read_only"] is True
