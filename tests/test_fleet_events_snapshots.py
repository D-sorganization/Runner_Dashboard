"""Tests for fleet status → NodeSnapshot flattening."""

from __future__ import annotations

import pytest

from fleet_events_snapshots import nodes_from_fleet_status


def test_flatten_fleet_status_online_node() -> None:
    status = {
        "NodeA": {
            "status": "online",
            "disk": {"windows_host": {"free_gb": 123.4, "percent": 55.0}},
        }
    }
    nodes = nodes_from_fleet_status(status)
    assert len(nodes) == 1
    assert nodes[0].name == "NodeA"
    assert nodes[0].online is True
    assert nodes[0].disk_free_gb == pytest.approx(123.4)
    assert nodes[0].disk_percent == pytest.approx(55.0)


def test_flatten_fleet_status_offline_disk_pressure() -> None:
    status = {
        "NodeB": {
            "status": "offline",
            "offline_reason": "disk-pressure",
            "offline_detail": "free space 4.0 GB <= minimum 20 GB",
        }
    }
    node = nodes_from_fleet_status(status)[0]
    assert node.online is False
    assert node.offline_reason == "disk-pressure"


def test_flatten_falls_back_to_wsl_disk() -> None:
    status = {"NodeC": {"status": "online", "disk": {"free_gb": 9.0, "percent": 95.0}}}
    node = nodes_from_fleet_status(status)[0]
    assert node.disk_free_gb == pytest.approx(9.0)
    assert node.disk_percent == pytest.approx(95.0)


def test_flatten_fleet_status_disk_cordon_flag() -> None:
    status = {
        "HostX": {
            "status": "online",
            "disk_full_cordon": {"active": True, "phase": "cordoned"},
        }
    }
    node = nodes_from_fleet_status(status)[0]
    assert node.disk_cordon_active is True
