"""rd-barb per-node forwarder primitive (#3117 slice S3, part of #3064).

Every node (leader, standby and plain forwarder) runs this: it turns a deterministic local
signal (host health, an auth problem, a stuck lane) into a sealed rd-threads envelope
(:mod:`scripts.rd_threads_envelope`) and hands it to an injectable ``Sender`` rather than
opening its own transport (delivery itself is #3089).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from staff.barb_forwarder import (
    DEFAULT_STUCK_LANE_AFTER_SECONDS,
    ForwarderSignal,
    UnknownForwarderNodeError,
    build_alert,
    build_outbox_sender,
    confirm_forwarding_node,
    detect_stuck_lane,
    forward,
    signals_from_readyz,
)
from staff.barb_node_leader import NodeLeaderConfig, NodeRoleEntry
from staff.conversations import ConversationStore

from scripts import rd_threads_envelope as envelope


def _config() -> NodeLeaderConfig:
    return NodeLeaderConfig(
        nodes=(
            NodeRoleEntry(name="hub-a", role="leader", priority=1),
            NodeRoleEntry(name="node-a", role="standby", priority=2),
            NodeRoleEntry(name="node-c", role="forwarder"),
        )
    )


# ── confirm_forwarding_node: every role forwards, unknown nodes fail closed ──
@pytest.mark.unit
@pytest.mark.parametrize(("node", "role"), [("hub-a", "leader"), ("node-a", "standby"), ("node-c", "forwarder")])
def test_confirm_forwarding_node_accepts_every_configured_role(node: str, role: str) -> None:
    assert confirm_forwarding_node(node, _config()) == role


@pytest.mark.unit
def test_confirm_forwarding_node_rejects_unknown_node() -> None:
    with pytest.raises(UnknownForwarderNodeError, match="not in the node-roles config"):
        confirm_forwarding_node("Mystery", _config())


# ── build_alert: a valid rd-threads alert envelope (pre-seal) ───────────────
@pytest.mark.unit
def test_build_alert_produces_a_valid_rd_threads_alert() -> None:
    signal = ForwarderSignal(kind="host_health", detail="disk at 97%", recommended_action="Free disk space.")
    alert = build_alert("node-c", signal)
    sealed = envelope.seal(alert, now=1_000.0)
    assert envelope.validate(sealed) == []
    assert sealed["kind"] == "alert"
    assert sealed["from"] == "forwarder:node-c"
    assert sealed["to"] == "rd-barb"
    assert "node-c" in sealed["summary"]


@pytest.mark.unit
def test_build_alert_rejects_unknown_signal_kind() -> None:
    bad = ForwarderSignal(kind="mystery", detail="x", recommended_action="y")
    with pytest.raises(ValueError, match="unknown forwarder signal kind"):
        build_alert("node-c", bad)


# ── forward(): confirm role, seal, hand to an injectable Sender ─────────────
@pytest.mark.unit
def test_forward_seals_the_envelope_and_hands_it_to_the_sender() -> None:
    sent: list[dict] = []
    signal = ForwarderSignal(kind="auth_problem", detail="gh token missing", recommended_action="Reissue the token.")
    result = forward("node-c", signal, sent.append, config=_config(), now=1_000.0)
    assert sent == [result]
    assert envelope.validate(result) == []
    assert result["from"] == "forwarder:node-c"


@pytest.mark.unit
def test_forward_is_idempotent_within_the_same_dedupe_bucket() -> None:
    sent: list[dict] = []
    signal = ForwarderSignal(kind="stuck_lane", detail="lane wedged", recommended_action="Check the claim.")
    first = forward("node-c", signal, sent.append, config=_config(), now=1_000.0)
    second = forward("node-c", signal, sent.append, config=_config(), now=1_010.0)
    assert first["id"] == second["id"]


@pytest.mark.unit
def test_forward_fails_closed_before_sending_for_an_unknown_node() -> None:
    sent: list[dict] = []
    signal = ForwarderSignal(kind="host_health", detail="x", recommended_action="y")
    with pytest.raises(UnknownForwarderNodeError):
        forward("Mystery", signal, sent.append, config=_config(), now=1_000.0)
    assert sent == []


# ── signals_from_readyz: reclassify readiness.py's own probe output (DRY) ───
@pytest.mark.unit
def test_signals_from_readyz_skips_ok_checks() -> None:
    checks = {"github_token": "ok", "gh_cli": "ok", "lease_db": "ok"}
    assert signals_from_readyz("node-c", checks) == []


@pytest.mark.unit
def test_signals_from_readyz_classifies_auth_vs_host_health() -> None:
    checks = {
        "github_token": {"status": "down", "detail": "GH_TOKEN env var not set"},
        "lease_db": {"status": "down", "detail": "sqlite read failed"},
        "gh_cli": "ok",
    }
    signals = signals_from_readyz("node-c", checks)
    by_kind = {s.kind: s for s in signals}
    assert set(by_kind) == {"auth_problem", "host_health"}
    assert "GH_TOKEN" in by_kind["auth_problem"].detail
    assert "sqlite read failed" in by_kind["host_health"].detail


@pytest.mark.unit
def test_signals_from_readyz_handles_bare_string_status_without_detail() -> None:
    signals = signals_from_readyz("node-c", {"lease_db": "down"})
    assert len(signals) == 1 and signals[0].kind == "host_health" and "lease_db" in signals[0].detail


# ── detect_stuck_lane: deterministic staleness, no model calls ──────────────
@pytest.mark.unit
def test_detect_stuck_lane_returns_none_when_heartbeat_is_fresh() -> None:
    assert detect_stuck_lane("lane-1", last_heartbeat_at=990.0, now=1_000.0) is None


@pytest.mark.unit
def test_detect_stuck_lane_returns_a_signal_once_stale() -> None:
    now = 10_000.0
    signal = detect_stuck_lane("lane-1", last_heartbeat_at=now - DEFAULT_STUCK_LANE_AFTER_SECONDS - 1, now=now)
    assert signal is not None
    assert signal.kind == "stuck_lane" and "lane-1" in signal.detail


@pytest.mark.unit
def test_detect_stuck_lane_rejects_empty_lane_id() -> None:
    with pytest.raises(ValueError, match="lane_id"):
        detect_stuck_lane("", last_heartbeat_at=0.0, now=1.0)


@pytest.mark.unit
def test_detect_stuck_lane_rejects_non_positive_threshold() -> None:
    with pytest.raises(ValueError, match="stale_after_seconds"):
        detect_stuck_lane("lane-1", last_heartbeat_at=0.0, now=1.0, stale_after_seconds=0)


# ── build_outbox_sender: reuse the existing bounded outbox, don't reinvent it ─
@pytest.mark.unit
def test_build_outbox_sender_enqueues_the_sealed_envelope_through_the_existing_outbox(tmp_path: Path) -> None:
    cs = ConversationStore(tmp_path / "node.sqlite3")
    outbox = cs.barb_outbox(max_entries=50, max_bytes=1_000_000, max_entry_bytes=10_000, rng=random.Random(1))
    send = build_outbox_sender(outbox, node="node-c")
    signal = ForwarderSignal(kind="host_health", detail="disk low", recommended_action="Free space.")
    sealed = forward("node-c", signal, send, config=_config(), now=1_000.0)
    pending = outbox.pending_for("forwarder:node-c", sealed["id"])
    assert len(pending) == 1 and pending[0].payload == sealed
