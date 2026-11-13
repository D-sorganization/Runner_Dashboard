"""role-sync slice 7 (#3100): capability map, visible decline, rd-threads relay, host flip."""

from __future__ import annotations

import time
from typing import Any

import pytest
from staff.role_sync_host import (
    CapabilityCategory,
    HostKind,
    PendingQuestion,
    RoleSyncConfig,
    active_host_for_role,
    decline_for_miss,
    host_has_capability,
    parse_role_sync_config,
    relay_capability_miss,
    restore_host_flip_snapshot,
    snapshot_for_host_flip,
    snapshot_to_dict,
)

from scripts import rd_threads_envelope as rd_env

GROK, RD = HostKind.GROKBOT, HostKind.RUNNER_DASHBOARD
_GROKBOT_CATS = (
    CapabilityCategory.MAIL_CONNECTOR,
    CapabilityCategory.CREDENTIAL_VAULT,
    CapabilityCategory.CHAT_QUESTION_CARDS,
)
_RD_CATS = (CapabilityCategory.STAFF_API, CapabilityCategory.RUNNERS)


def test_host_capability_matrix() -> None:
    for cat in _GROKBOT_CATS:
        assert host_has_capability(GROK, cat) and not host_has_capability(RD, cat)
    for cat in _RD_CATS:
        assert host_has_capability(RD, cat) and not host_has_capability(GROK, cat)


def test_capability_miss_produces_visible_notice() -> None:
    decline = decline_for_miss(
        role="night-watch", category=CapabilityCategory.STAFF_API, active_host=GROK, config=RoleSyncConfig()
    )
    assert decline.notice and decline.peer_host == RD


def test_household_data_refused_on_rd_without_opt_in() -> None:
    decline = decline_for_miss(
        role="household-assistant",
        category=CapabilityCategory.HOUSEHOLD_PERSONAL_DATA,
        active_host=RD,
        config=RoleSyncConfig(household_data_opt_in=False),
    )
    assert decline.relay_eligible is False and "household" in decline.notice.lower()


def test_household_data_allowed_on_rd_with_opt_in() -> None:
    cfg = RoleSyncConfig(household_data_opt_in=True)
    assert host_has_capability(RD, CapabilityCategory.HOUSEHOLD_PERSONAL_DATA, config=cfg)


class _RecordingRelay:
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []

    def send_rd_threads(self, envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(envelope)
        return {"ok": True}


def test_approved_relay_routes_via_rd_threads_client() -> None:
    relay = _RecordingRelay()
    decline = decline_for_miss(
        role="night-watch",
        category=CapabilityCategory.MAIL_CONNECTOR,
        active_host=RD,
        config=RoleSyncConfig(relay_handoff_approved=True),
    )
    result = relay_capability_miss(relay, decline=decline, from_agent="agent-test", to_role="night-watch", now=1.7e9)
    assert result["ok"]
    env = relay.calls[0]
    assert not rd_env.validate(env) and env["kind"] == "update" and env["to"] == "night-watch"


def test_unapproved_relay_refused_without_client_io() -> None:
    relay = _RecordingRelay()
    decline = decline_for_miss(
        role="night-watch",
        category=CapabilityCategory.MAIL_CONNECTOR,
        active_host=RD,
        config=RoleSyncConfig(relay_handoff_approved=False),
    )
    out = relay_capability_miss(relay, decline=decline, from_agent="agent-test", to_role="night-watch", now=time.time())
    assert out["error"] == "relay_not_approved" and relay.calls == []


def test_host_flip_snapshot_round_trip_preserves_recommended_option() -> None:
    question = PendingQuestion(
        correlation_id="c_demo",
        summary="Approve dry run?",
        options=({"label": "Yes", "recommended": True}, {"label": "No", "recommended": False}),
        recommended="Yes",
    )
    snap = snapshot_for_host_flip(
        role="work-assistant",
        source_host=GROK,
        target_host=RD,
        permitted_state={"session_epoch": 3},
        pending_questions=(question,),
        in_flight_work=({"kind": "triage", "id": "wi-1"},),
    )
    assert snap.holds_immutable_note
    restored = restore_host_flip_snapshot(snap)
    assert restored.pending_questions[0].recommended == "Yes"
    assert snapshot_to_dict(restored)["pending_questions"][0]["recommended"] == "Yes"


def test_parse_role_sync_config_from_role_yaml_block() -> None:
    cfg = parse_role_sync_config({"role_sync": {"relay_handoff_approved": True, "preferred_host": "runner_dashboard"}})
    assert cfg.relay_handoff_approved and active_host_for_role(cfg) == RD


@pytest.mark.parametrize("bad", ["", "   "])
def test_snapshot_precondition_rejects_empty_role(bad: str) -> None:
    with pytest.raises(ValueError):
        snapshot_for_host_flip(
            role=bad, source_host=GROK, target_host=RD, permitted_state={}, pending_questions=(), in_flight_work=()
        )
