"""role-sync slice 7 (#3100): per-role/host capability map, visible decline, rd-threads relay, host flip."""

from __future__ import annotations

import time
from typing import Any

import pytest
from scripts import rd_threads_envelope as rd_env
from staff.role_sync_capabilities import (
    CapabilityCategory,
    HostKind,
    RoleSyncConfig,
    active_host_for_role,
    decline_for_miss,
    host_has_capability,
    parse_role_sync_config,
    requires_household_opt_in,
)
from staff.role_sync_handoff import (
    HostFlipSnapshot,
    PendingQuestion,
    relay_capability_miss,
    restore_host_flip_snapshot,
    snapshot_for_host_flip,
)

GROK = HostKind.GROKBOT
RD = HostKind.RUNNER_DASHBOARD


def test_grokbot_only_categories_not_on_rd() -> None:
    for cat in (
        CapabilityCategory.MAIL_CONNECTOR,
        CapabilityCategory.CALENDAR_CONNECTOR,
        CapabilityCategory.DRIVE_CONNECTOR,
        CapabilityCategory.CREDENTIAL_VAULT,
        CapabilityCategory.ROUTINES,
        CapabilityCategory.CHAT_QUESTION_CARDS,
    ):
        assert host_has_capability(GROK, cat)
        assert not host_has_capability(RD, cat)


def test_rd_only_categories_not_on_grokbot() -> None:
    for cat in (
        CapabilityCategory.PERSISTENT_CLI_SESSIONS,
        CapabilityCategory.RUNNERS,
        CapabilityCategory.STAFF_API,
    ):
        assert host_has_capability(RD, cat)
        assert not host_has_capability(GROK, cat)


def test_capability_miss_produces_visible_notice() -> None:
    decline = decline_for_miss(
        role="night-watch",
        category=CapabilityCategory.STAFF_API,
        active_host=GROK,
        config=RoleSyncConfig(),
    )
    assert decline.notice
    assert "staff_api" in decline.notice or "staff api" in decline.notice.lower()
    assert decline.active_host == GROK
    assert decline.category == CapabilityCategory.STAFF_API
    assert decline.peer_host == RD


def test_household_data_refused_on_rd_without_opt_in() -> None:
    decline = decline_for_miss(
        role="household-assistant",
        category=CapabilityCategory.HOUSEHOLD_PERSONAL_DATA,
        active_host=RD,
        config=RoleSyncConfig(household_data_opt_in=False),
    )
    assert decline.relay_eligible is False
    assert "opt" in decline.notice.lower() or "household" in decline.notice.lower()


def test_household_data_allowed_on_rd_with_opt_in() -> None:
    assert host_has_capability(
        RD,
        CapabilityCategory.HOUSEHOLD_PERSONAL_DATA,
        config=RoleSyncConfig(household_data_opt_in=True),
    )


class _RecordingRelay:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_rd_threads(self, envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(envelope)
        return {"ok": True, "status": "sent", "correlation_id": envelope.get("correlation_id")}


def test_approved_relay_routes_via_rd_threads_client() -> None:
    relay = _RecordingRelay()
    decline = decline_for_miss(
        role="night-watch",
        category=CapabilityCategory.MAIL_CONNECTOR,
        active_host=RD,
        config=RoleSyncConfig(relay_handoff_approved=True),
    )
    result = relay_capability_miss(
        relay,
        decline=decline,
        from_agent="agent-test",
        to_role="night-watch",
        now=1_700_000_000.0,
    )
    assert result["ok"] is True
    assert len(relay.calls) == 1
    env = relay.calls[0]
    assert not rd_env.validate(env)
    assert env["kind"] == "update"
    assert env["to"] == "night-watch"


def test_unapproved_relay_refused_without_client_io() -> None:
    relay = _RecordingRelay()
    decline = decline_for_miss(
        role="night-watch",
        category=CapabilityCategory.MAIL_CONNECTOR,
        active_host=RD,
        config=RoleSyncConfig(relay_handoff_approved=False),
    )
    result = relay_capability_miss(
        relay,
        decline=decline,
        from_agent="agent-test",
        to_role="night-watch",
        now=time.time(),
    )
    assert result["ok"] is False
    assert result.get("error") == "relay_not_approved"
    assert relay.calls == []


def test_host_flip_snapshot_round_trip_preserves_recommended_option() -> None:
    question = PendingQuestion(
        correlation_id="c_demo",
        summary="Approve dry run?",
        options=(
            {"label": "Yes", "recommended": True},
            {"label": "No", "recommended": False},
        ),
        recommended="Yes",
        task_state="input-required",
    )
    snap = snapshot_for_host_flip(
        role="work-assistant",
        source_host=GROK,
        target_host=RD,
        permitted_state={"session_epoch": 3, "open_task": "inbox-triage"},
        pending_questions=(question,),
        in_flight_work=({"kind": "triage", "id": "wi-1"},),
    )
    assert "hold" not in str(snap.to_dict()).lower() or snap.holds_immutable_note
    restored = restore_host_flip_snapshot(snap)
    assert restored.pending_questions[0].recommended == "Yes"
    assert restored.pending_questions[0].options[0]["recommended"] is True
    assert restored.permitted_state["session_epoch"] == 3


def test_parse_role_sync_config_from_role_yaml_block() -> None:
    cfg = parse_role_sync_config(
        {
            "role_sync": {
                "relay_handoff_approved": True,
                "household_data_opt_in": True,
                "preferred_host": "runner_dashboard",
            }
        }
    )
    assert cfg.relay_handoff_approved is True
    assert cfg.household_data_opt_in is True
    assert active_host_for_role(cfg) == RD


@pytest.mark.parametrize("bad", ["", "   "])
def test_snapshot_precondition_rejects_empty_role(bad: str) -> None:
    with pytest.raises(ValueError):
        snapshot_for_host_flip(
            role=bad,
            source_host=GROK,
            target_host=RD,
            permitted_state={},
            pending_questions=(),
            in_flight_work=(),
        )
