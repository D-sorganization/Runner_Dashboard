"""Fleet feed events for disk-full cordon (RD-OPS-017)."""

from __future__ import annotations

from disk_full_cordon_fleet_events import (
    disk_cordon_active_from_fleet_node,
    multi_host_disk_cordon_fleet_events,
)
from fleet_events import NodeSnapshot


def test_multi_host_disk_cordon_event_emitted() -> None:
    previous = [
        NodeSnapshot(name="A", online=True, disk_cordon_active=True),
        NodeSnapshot(name="B", online=True, disk_cordon_active=False),
    ]
    current = [
        NodeSnapshot(name="A", online=True, disk_cordon_active=True),
        NodeSnapshot(name="B", online=True, disk_cordon_active=True),
    ]
    events = multi_host_disk_cordon_fleet_events(previous, current, ts=1, alert_threshold=2)
    assert len(events) == 1
    assert events[0].kind == "multi_host_disk_cordon"
    assert events[0].severity == "critical"


def test_multi_host_disk_cordon_suppressed_below_threshold() -> None:
    current = [NodeSnapshot(name="A", online=True, disk_cordon_active=True)]
    assert multi_host_disk_cordon_fleet_events(None, current, ts=1, alert_threshold=2) == []


def test_disk_cordon_active_from_fleet_node_payload() -> None:
    assert disk_cordon_active_from_fleet_node({"disk_full_cordon": {"active": True}}) is True
    assert disk_cordon_active_from_fleet_node({"disk_full_cordon": {"active": False}}) is False
    assert disk_cordon_active_from_fleet_node({}) is False
