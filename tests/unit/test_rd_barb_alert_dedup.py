"""rd-barb cross-node alert dedup + status surfacing (#3117 slice S4, part of #3064).

Covers :func:`cross_node_alert_key`, :class:`AlertDeduplicator` and :func:`surface_alert` from
:mod:`staff.rd_barb_alert_dedup`.
"""

from __future__ import annotations

import pytest
from fleet_events import EventStore
from staff.barb_forwarder import ForwarderSignal, build_alert, confirm_forwarding_node
from staff.barb_node_leader import NodeLeaderConfig, NodeRoleEntry
from staff.rd_barb_alert_dedup import (
    ALERT_EVENT_KIND,
    AlertDeduplicator,
    cross_node_alert_key,
    surface_alert,
)

from scripts import rd_threads_envelope as envelope


def _config() -> NodeLeaderConfig:
    return NodeLeaderConfig(
        nodes=(
            NodeRoleEntry(name="DeskComputer", role="leader", priority=1),
            NodeRoleEntry(name="ControlTower", role="standby", priority=2),
        )
    )


def _sealed_alert(node: str, *, detail: str, now: float) -> dict:
    confirm_forwarding_node(node, _config())
    signal = ForwarderSignal(kind="auth_problem", detail=detail, recommended_action="Reissue the token.")
    return envelope.seal(build_alert(node, signal), now)


# ── cross_node_alert_key: identity is kind + normalized body, not the node ──
@pytest.mark.unit
def test_cross_node_alert_key_matches_across_nodes_for_the_same_condition() -> None:
    desk = _sealed_alert("DeskComputer", detail="GH_TOKEN env var not set", now=1_000.0)
    tower = _sealed_alert("ControlTower", detail="GH_TOKEN env var not set", now=1_050.0)
    assert desk["from"] != tower["from"]
    assert cross_node_alert_key(desk) == cross_node_alert_key(tower)


@pytest.mark.unit
def test_cross_node_alert_key_differs_for_a_different_condition() -> None:
    a = _sealed_alert("DeskComputer", detail="GH_TOKEN env var not set", now=1_000.0)
    b = _sealed_alert("DeskComputer", detail="sqlite read failed", now=1_000.0)
    assert cross_node_alert_key(a) != cross_node_alert_key(b)


@pytest.mark.unit
def test_cross_node_alert_key_normalizes_digit_runs() -> None:
    stale_short = {"kind": "alert", "body": "lane 'x' has not heartbeat in 3601s (threshold 3600s)"}
    stale_long = {"kind": "alert", "body": "lane 'x' has not heartbeat in 9999s (threshold 3600s)"}
    assert cross_node_alert_key(stale_short) == cross_node_alert_key(stale_long)


@pytest.mark.unit
def test_cross_node_alert_key_is_case_and_whitespace_insensitive() -> None:
    a = {"kind": "alert", "body": "Disk  Low"}
    b = {"kind": "alert", "body": "disk low"}
    assert cross_node_alert_key(a) == cross_node_alert_key(b)


# ── AlertDeduplicator: debounce by key within a time window (mirrors notify_dead) ──
@pytest.mark.unit
def test_deduplicator_surfaces_the_first_occurrence_of_a_key() -> None:
    dedup = AlertDeduplicator(window_seconds=300.0)
    envelope_ = {"kind": "alert", "body": "GH_TOKEN env var not set"}
    assert dedup.should_surface(envelope_, now=1_000.0) is True


@pytest.mark.unit
def test_deduplicator_suppresses_a_repeat_within_the_window() -> None:
    dedup = AlertDeduplicator(window_seconds=300.0)
    envelope_ = {"kind": "alert", "body": "GH_TOKEN env var not set"}
    assert dedup.should_surface(envelope_, now=1_000.0) is True
    assert dedup.should_surface(envelope_, now=1_200.0) is False


@pytest.mark.unit
def test_deduplicator_surfaces_again_once_the_window_elapses() -> None:
    dedup = AlertDeduplicator(window_seconds=300.0)
    envelope_ = {"kind": "alert", "body": "GH_TOKEN env var not set"}
    assert dedup.should_surface(envelope_, now=1_000.0) is True
    assert dedup.should_surface(envelope_, now=1_301.0) is True


@pytest.mark.unit
def test_deduplicator_treats_different_keys_independently() -> None:
    dedup = AlertDeduplicator(window_seconds=300.0)
    a = {"kind": "alert", "body": "GH_TOKEN env var not set"}
    b = {"kind": "alert", "body": "sqlite read failed"}
    assert dedup.should_surface(a, now=1_000.0) is True
    assert dedup.should_surface(b, now=1_000.0) is True


@pytest.mark.unit
def test_deduplicator_reset_clears_debounce_state() -> None:
    dedup = AlertDeduplicator(window_seconds=300.0)
    envelope_ = {"kind": "alert", "body": "GH_TOKEN env var not set"}
    assert dedup.should_surface(envelope_, now=1_000.0) is True
    dedup.reset()
    assert dedup.should_surface(envelope_, now=1_001.0) is True


# ── surface_alert: record a FleetEvent, deduped across nodes, via the existing store ──
@pytest.mark.unit
def test_surface_alert_records_a_fleet_event_on_first_occurrence() -> None:
    store = EventStore()
    dedup = AlertDeduplicator(window_seconds=300.0)
    sealed = _sealed_alert("DeskComputer", detail="GH_TOKEN env var not set", now=1_000.0)
    event = surface_alert(sealed, dedup, now=1_000.0, store=store)
    assert event is not None
    assert event.kind == ALERT_EVENT_KIND
    assert event.severity == "warning"
    assert event.node == "DeskComputer"
    assert "GH_TOKEN" in event.detail
    assert list(store.recent()) == [event]


@pytest.mark.unit
def test_surface_alert_suppresses_a_duplicate_from_a_different_node() -> None:
    store = EventStore()
    dedup = AlertDeduplicator(window_seconds=300.0)
    first = _sealed_alert("DeskComputer", detail="GH_TOKEN env var not set", now=1_000.0)
    second = _sealed_alert("ControlTower", detail="GH_TOKEN env var not set", now=1_010.0)
    assert surface_alert(first, dedup, now=1_000.0, store=store) is not None
    assert surface_alert(second, dedup, now=1_010.0, store=store) is None
    assert len(store.recent()) == 1


@pytest.mark.unit
def test_surface_alert_surfaces_a_genuinely_different_condition() -> None:
    store = EventStore()
    dedup = AlertDeduplicator(window_seconds=300.0)
    first = _sealed_alert("DeskComputer", detail="GH_TOKEN env var not set", now=1_000.0)
    second = _sealed_alert("DeskComputer", detail="sqlite read failed", now=1_000.0)
    assert surface_alert(first, dedup, now=1_000.0, store=store) is not None
    assert surface_alert(second, dedup, now=1_000.0, store=store) is not None
    assert len(store.recent()) == 2
