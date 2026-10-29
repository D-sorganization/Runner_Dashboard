"""Usage SSoT: ``provider-availability.v1`` ingest contract and fleet view (#3042).

Usage-Tracker POSTs each hourly snapshot to this node; these tests cover the
ingest contract (schema, bounds, newest-wins) and the per-machine staleness
view the Plan quota panel renders, independent of any HTTP wiring.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from staff import usage_snapshot as snap_mod

NOW = datetime(2026, 10, 26, 17, 30, tzinfo=UTC)

FIXTURE = {
    "schema_version": "provider-availability.v1",
    "checked_at": "2026-10-26T17:20:00Z",
    "providers": [
        {
            "provider": "codex",
            "label": "Codex",
            "status": "ok",
            "quota_used_pct": 7.0,
            "quota_limit": 100,
            "reset_at": None,
            "near_limit_flag": False,
            "rate_limits": {
                "readings": [
                    {
                        "machine": "hub-a",
                        "observed_at": "2026-10-26T17:19:00Z",
                        "used_percent": 7.0,
                        "resets_at": None,
                        "source": "codex-session-log",
                    },
                    {
                        "machine": "node-a",
                        "observed_at": "2026-10-24T22:25:00Z",
                        "used_percent": 37.0,
                        "resets_at": None,
                        "source": "codex-session-log",
                    },
                    {
                        "machine": "node-b",
                        "observed_at": "2026-10-24T20:09:00Z",
                        "used_percent": 35.0,
                        "resets_at": None,
                        "source": "codex-session-log",
                    },
                ]
            },
        },
        {
            "provider": "claude",
            "label": "Claude",
            "status": "no_meter",
            "quota_used_pct": None,
            "quota_limit": None,
            "reset_at": None,
            "near_limit_flag": False,
            "rate_limits": {"readings": []},
        },
    ],
}


@pytest.mark.unit
def test_snapshot_contract_accepts_fixture() -> None:
    snapshot = snap_mod.validate_payload(FIXTURE, now=NOW)
    assert snapshot.schema_version == "provider-availability.v1"
    assert snapshot.checked_at == datetime(2026, 10, 26, 17, 20, tzinfo=UTC)
    providers = {p["provider"]: p for p in snapshot.providers}
    assert providers["codex"]["quota_used_pct"] == 7.0
    assert len(providers["codex"]["readings"]) == 3
    assert providers["claude"]["quota_used_pct"] is None
    # Round-trips through to_dict/from_dict unchanged.
    restored = snap_mod.UsageSnapshot.from_dict(snapshot.to_dict())
    assert restored == snapshot


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (lambda d: d.update(schema_version="provider-availability.v2"), "schema_version"),
        (lambda d: d.update(checked_at=""), "checked_at"),
        (lambda d: d.update(checked_at="not-a-timestamp"), "checked_at"),
        (lambda d: d.update(checked_at="2026-10-26T20:00:00Z"), "checked_at"),  # > 10 min in the future
        (lambda d: d.update(providers="not-a-list"), "providers"),
        (lambda d: d["providers"].__setitem__(0, {**d["providers"][0], "provider": ""}), "providers[0].provider"),
        (lambda d: d["providers"].__setitem__(0, {**d["providers"][0], "quota_used_pct": 150}), "quota_used_pct"),
        (
            lambda d: d["providers"][0]["rate_limits"]["readings"].__setitem__(
                0, {**d["providers"][0]["rate_limits"]["readings"][0], "used_percent": -1}
            ),
            "used_percent",
        ),
        (
            lambda d: d["providers"][0]["rate_limits"]["readings"].__setitem__(
                0, {**d["providers"][0]["rate_limits"]["readings"][0], "machine": ""}
            ),
            "machine",
        ),
    ],
)
def test_snapshot_rejects_bad_payload(mutation: Callable[[dict[str, Any]], Any], field: str) -> None:
    import copy

    bad = copy.deepcopy(FIXTURE)
    mutation(bad)
    with pytest.raises(snap_mod.SnapshotRejected) as excinfo:
        snap_mod.validate_payload(bad, now=NOW)
    assert field in excinfo.value.field


@pytest.mark.unit
def test_snapshot_keeps_newest(tmp_path: Path) -> None:
    store = snap_mod.UsageSnapshotStore(tmp_path / "s.json")
    newer = snap_mod.validate_payload(FIXTURE, now=NOW)
    older_raw = {**FIXTURE, "checked_at": "2026-10-26T16:00:00Z"}
    older = snap_mod.validate_payload(older_raw, now=NOW)

    assert store.record(newer) is True
    assert store.record(older) is False  # never overwrite a newer snapshot with an older one
    reloaded = snap_mod.UsageSnapshotStore(tmp_path / "s.json").get()
    assert reloaded is not None and reloaded.checked_at == newer.checked_at


@pytest.mark.unit
def test_snapshot_view_flags_stale_machine(tmp_path: Path) -> None:
    store = snap_mod.UsageSnapshotStore(tmp_path / "s.json")
    snapshot = snap_mod.validate_payload(FIXTURE, now=NOW)
    store.record(snapshot)

    body = snap_mod.view(NOW, store=store)
    assert body["snapshot_stale"] is False  # received just now
    machines = {m["machine"]: m for m in body["providers"][0]["machines"]}
    assert machines["hub-a"]["stale"] is False
    assert machines["node-a"]["stale"] is True
    assert machines["node-b"]["stale"] is True
    assert machines["hub-a"]["age_seconds"] == pytest.approx(11 * 60, abs=1)


@pytest.mark.unit
def test_snapshot_get_empty(tmp_path: Path) -> None:
    store = snap_mod.UsageSnapshotStore(tmp_path / "missing.json")
    body = snap_mod.view(NOW, store=store)
    assert body == {
        "checked_at": None,
        "received_at": None,
        "snapshot_age_seconds": None,
        "snapshot_stale": True,
        "providers": [],
    }


@pytest.mark.unit
def test_snapshot_overall_stale_when_received_long_ago(tmp_path: Path) -> None:
    store = snap_mod.UsageSnapshotStore(tmp_path / "s.json")
    received_long_ago = snap_mod.validate_payload(FIXTURE, now=NOW)
    old_snapshot = snap_mod.UsageSnapshot(
        schema_version=received_long_ago.schema_version,
        checked_at=received_long_ago.checked_at,
        received_at=NOW - timedelta(hours=3),
        providers=received_long_ago.providers,
    )
    store.record(old_snapshot)
    body = snap_mod.view(NOW, store=store)
    assert body["snapshot_stale"] is True
    assert body["snapshot_age_seconds"] == pytest.approx(3 * 3600, abs=1)


@pytest.mark.unit
def test_store_tolerates_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    store = snap_mod.UsageSnapshotStore(path)
    assert store.get() is None
    snapshot = snap_mod.validate_payload(FIXTURE, now=NOW)
    assert store.record(snapshot) is True and store.get() == snapshot
