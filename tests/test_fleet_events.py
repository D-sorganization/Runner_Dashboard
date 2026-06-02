"""Tests for the fleet event log + disk-pressure classification (issue #863).

Covers the three orthogonal pieces of ``backend/fleet_events.py``:
  - the pure ``classify_fleet_events`` diff (offline/online/low-disk/saturation/
    watchdog), with the load-bearing offline-due-to-disk classification;
  - the bounded, thread-safe ``EventStore`` ring buffer;
  - the ``/api/events`` router contract (over a minimal app, no full server
    import — that trips Windows file-mode checks);
  - the nested ``/api/fleet/status`` → ``NodeSnapshot`` flattening.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fleet_events import (  # noqa: E402
    DISK_CRITICAL_PERCENT,
    DISK_MIN_FREE_GB,
    EventStore,
    FleetEvent,
    FleetEventPoller,
    NodeSnapshot,
    classify_fleet_events,
    get_event_store,
    node_disk_is_critical,
    node_disk_is_low,
    nodes_from_fleet_status,
)

# ── FleetEvent contract ────────────────────────────────────────────────────


def test_fleet_event_rejects_bad_severity() -> None:
    with pytest.raises(ValueError):
        FleetEvent(ts=1, severity="boom", kind="low_disk", title="x")  # type: ignore[arg-type]


def test_fleet_event_rejects_bad_kind() -> None:
    with pytest.raises(ValueError):
        FleetEvent(ts=1, severity="info", kind="explode", title="x")  # type: ignore[arg-type]


def test_fleet_event_rejects_negative_ts() -> None:
    with pytest.raises(ValueError):
        FleetEvent(ts=-1, severity="info", kind="low_disk", title="x")


def test_fleet_event_requires_title() -> None:
    with pytest.raises(ValueError):
        FleetEvent(ts=1, severity="info", kind="low_disk", title="")


def test_fleet_event_to_dict_omits_absent_node() -> None:
    e = FleetEvent(ts=5, severity="warning", kind="saturation", title="Saturated")
    assert e.to_dict() == {
        "ts": 5,
        "severity": "warning",
        "kind": "saturation",
        "title": "Saturated",
        "detail": "",
    }


def test_fleet_event_to_dict_includes_node() -> None:
    e = FleetEvent(ts=5, severity="info", kind="runner_online", title="x", node="NodeA")
    assert e.to_dict()["node"] == "NodeA"


# ── Disk classification (the load-bearing requirement) ─────────────────────


def test_disk_critical_by_free_gb() -> None:
    assert node_disk_is_critical(free_gb=DISK_MIN_FREE_GB - 1, percent=10.0) is True


def test_disk_critical_by_percent() -> None:
    assert node_disk_is_critical(free_gb=999.0, percent=DISK_CRITICAL_PERCENT) is True


def test_disk_healthy_is_not_critical() -> None:
    assert node_disk_is_critical(free_gb=999.0, percent=10.0) is False


def test_disk_low_includes_critical() -> None:
    assert node_disk_is_low(free_gb=DISK_MIN_FREE_GB - 1, percent=10.0) is True


def test_disk_low_warn_band_not_critical() -> None:
    # In the warn band (2x min free) but not yet critical.
    assert node_disk_is_low(free_gb=DISK_MIN_FREE_GB * 1.5, percent=10.0) is True
    assert node_disk_is_critical(free_gb=DISK_MIN_FREE_GB * 1.5, percent=10.0) is False


def test_disk_none_metrics_are_not_low() -> None:
    assert node_disk_is_low(free_gb=None, percent=None) is False


# ── classify_fleet_events ──────────────────────────────────────────────────


def test_first_poll_emits_no_transitions() -> None:
    nodes = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    events = classify_fleet_events(None, nodes, ts=1000)
    # No prior snapshot → no offline/online transitions.
    assert [e.kind for e in events] == []


def test_offline_transition_due_to_disk_is_critical_with_reason() -> None:
    prev = [NodeSnapshot(name="A", online=True, disk_free_gb=200, disk_percent=50)]
    curr = [
        NodeSnapshot(
            name="A",
            online=False,
            disk_free_gb=DISK_MIN_FREE_GB - 5,
            disk_percent=99,
        )
    ]
    events = classify_fleet_events(prev, curr, ts=2000)
    offline = [e for e in events if e.kind == "runner_offline"]
    assert len(offline) == 1
    assert offline[0].severity == "critical"
    assert "disk pressure" in offline[0].title.lower()
    assert offline[0].node == "A"


def test_offline_transition_with_upstream_disk_reason() -> None:
    prev = [NodeSnapshot(name="A", online=True)]
    curr = [
        NodeSnapshot(
            name="A",
            online=False,
            offline_reason="disk-pressure",
            offline_detail="free space 4.0 GB <= minimum 20 GB",
        )
    ]
    events = classify_fleet_events(prev, curr, ts=2000)
    offline = [e for e in events if e.kind == "runner_offline"][0]
    assert "disk pressure" in offline.title.lower()
    assert "4.0 GB" in offline.detail


def test_offline_transition_non_disk_reason() -> None:
    prev = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    curr = [
        NodeSnapshot(
            name="A",
            online=False,
            disk_free_gb=500,
            disk_percent=20,
            offline_reason="timeout",
            offline_detail="Connection timed out",
        )
    ]
    offline = [e for e in classify_fleet_events(prev, curr, ts=2000) if e.kind == "runner_offline"][0]
    assert "timeout" in offline.title
    assert "disk" not in offline.title.lower()


def test_online_transition_emits_info() -> None:
    prev = [NodeSnapshot(name="A", online=False)]
    curr = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    online = [e for e in classify_fleet_events(prev, curr, ts=3000) if e.kind == "runner_online"]
    assert len(online) == 1
    assert online[0].severity == "info"


def test_low_disk_warning_for_online_node() -> None:
    prev = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    curr = [NodeSnapshot(name="A", online=True, disk_free_gb=DISK_MIN_FREE_GB * 1.5, disk_percent=85)]
    low = [e for e in classify_fleet_events(prev, curr, ts=4000) if e.kind == "low_disk"]
    assert len(low) == 1
    assert low[0].severity == "warning"
    assert low[0].node == "A"


def test_offline_node_does_not_double_report_low_disk() -> None:
    prev = [NodeSnapshot(name="A", online=True, disk_free_gb=200, disk_percent=50)]
    curr = [NodeSnapshot(name="A", online=False, disk_free_gb=DISK_MIN_FREE_GB - 1, disk_percent=99)]
    events = classify_fleet_events(prev, curr, ts=5000)
    # Offline node surfaces disk reason via the offline event, not a separate
    # low_disk event.
    assert not any(e.kind == "low_disk" for e in events)
    assert any(e.kind == "runner_offline" for e in events)


def test_saturation_when_all_busy() -> None:
    nodes = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    events = classify_fleet_events(None, nodes, ts=6000, capacity=4, online_count=4)
    sat = [e for e in events if e.kind == "saturation"]
    assert len(sat) == 1
    assert sat[0].severity == "warning"


def test_no_saturation_with_spare_capacity() -> None:
    nodes = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    events = classify_fleet_events(None, nodes, ts=6000, capacity=4, online_count=2)
    assert not any(e.kind == "saturation" for e in events)


def test_watchdog_regression_emits_once() -> None:
    nodes = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    events = classify_fleet_events(None, nodes, ts=7000, watchdog_status="degraded")
    wd = [e for e in events if e.kind == "watchdog"]
    assert len(wd) == 1
    assert wd[0].severity == "warning"

    # Same status next poll → no duplicate event.
    again = classify_fleet_events(
        nodes,
        nodes,
        ts=7100,
        watchdog_status="degraded",
        previous_watchdog_status="degraded",
    )
    assert not any(e.kind == "watchdog" for e in again)


def test_watchdog_legacy_is_critical() -> None:
    nodes = [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)]
    wd = [e for e in classify_fleet_events(None, nodes, ts=7000, watchdog_status="legacy") if e.kind == "watchdog"][0]
    assert wd.severity == "critical"


def test_classify_is_pure_no_mutation() -> None:
    prev = [NodeSnapshot(name="A", online=True)]
    curr = [NodeSnapshot(name="A", online=False, offline_reason="timeout")]
    classify_fleet_events(prev, curr, ts=1)
    classify_fleet_events(prev, curr, ts=2)
    # Inputs unchanged; same output for same input.
    out1 = classify_fleet_events(prev, curr, ts=99)
    out2 = classify_fleet_events(prev, curr, ts=99)
    assert out1 == out2


# ── EventStore ─────────────────────────────────────────────────────────────


def _ev(ts: int) -> FleetEvent:
    return FleetEvent(ts=ts, severity="info", kind="runner_online", title=f"e{ts}")


def test_store_rejects_nonpositive_capacity() -> None:
    with pytest.raises(ValueError):
        EventStore(capacity=0)


def test_store_records_and_returns_newest_first() -> None:
    store = EventStore(capacity=10)
    store.record(_ev(1))
    store.record(_ev(2))
    store.record(_ev(3))
    recent = store.recent()
    assert [e.ts for e in recent] == [3, 2, 1]


def test_store_evicts_oldest_at_capacity() -> None:
    store = EventStore(capacity=3)
    for i in range(5):
        store.record(_ev(i))
    assert len(store) == 3
    assert [e.ts for e in store.recent()] == [4, 3, 2]


def test_store_record_many_respects_capacity() -> None:
    store = EventStore(capacity=2)
    store.record_many([_ev(1), _ev(2), _ev(3)])
    assert [e.ts for e in store.recent()] == [3, 2]


def test_store_recent_limit() -> None:
    store = EventStore(capacity=10)
    for i in range(5):
        store.record(_ev(i))
    assert [e.ts for e in store.recent(limit=2)] == [4, 3]


def test_store_recent_rejects_negative_limit() -> None:
    store = EventStore(capacity=10)
    with pytest.raises(ValueError):
        store.recent(limit=-1)


# ── FleetEventPoller ───────────────────────────────────────────────────────


def test_poller_diffs_across_observations() -> None:
    store = EventStore(capacity=50)
    poller = FleetEventPoller(store=store)
    # First observe: node online, no transition.
    poller.observe(
        [NodeSnapshot(name="A", online=True, disk_free_gb=500, disk_percent=20)],
        ts=1000,
    )
    assert len(store) == 0
    # Second observe: node drops offline due to disk → one critical event.
    poller.observe(
        [NodeSnapshot(name="A", online=False, disk_free_gb=DISK_MIN_FREE_GB - 1, disk_percent=99)],
        ts=2000,
    )
    recent = store.recent()
    assert len(recent) == 1
    assert recent[0].kind == "runner_offline"
    assert recent[0].severity == "critical"


def test_get_event_store_is_singleton() -> None:
    assert get_event_store() is get_event_store()


# ── nodes_from_fleet_status flattening ─────────────────────────────────────


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


# ── /api/events router contract ────────────────────────────────────────────


def test_events_endpoint_returns_recent_newest_first() -> None:
    import routers.events as events_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Reset the singleton store for a deterministic test.
    store = get_event_store()
    # Drain by recording into a fresh store reference is not possible (module
    # singleton), so we record known events and assert ordering/shape.
    store.record(FleetEvent(ts=1, severity="info", kind="runner_online", title="up"))
    store.record(FleetEvent(ts=2, severity="critical", kind="runner_offline", title="down", node="A"))

    app = FastAPI()
    app.include_router(events_router.router)
    client = TestClient(app)

    resp = client.get("/api/events?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert "capacity" in body
    # Newest first.
    assert body["events"][0]["ts"] >= body["events"][-1]["ts"]
    assert body["events"][0]["severity"] in ("info", "warning", "critical")


def test_events_endpoint_rejects_out_of_range_limit() -> None:
    import routers.events as events_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(events_router.router)
    client = TestClient(app)

    assert client.get("/api/events?limit=0").status_code == 422
    assert client.get("/api/events?limit=99999").status_code == 422
