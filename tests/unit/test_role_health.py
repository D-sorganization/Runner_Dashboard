"""Unit tests for persona role heartbeat, 2-reading hysteresis health, and wake/resume envelopes (#3295 S3, #3296)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.role_health import (
    HEARTBEAT_INTERVAL_S,
    PROBE_MIN_SEPARATION_S,
    SLOT_LATE_THRESHOLD_S,
    RoleHealthStore,
    build_role_resume_envelope,
    build_role_unhealthy_envelope,
    notify_role_transition,
)

from scripts import rd_barb_notify, rd_threads_envelope


def test_unknown_or_corrupted_role_health_fails_to_degraded(tmp_path: Path) -> None:
    store = RoleHealthStore(tmp_path / "role-health.json")
    snap = store.evaluate_role("bob", now=1000.0)
    assert snap["state"] == "degraded" and snap["reasons"] == ["unknown_health"]

    (tmp_path / "role-health.json").write_text("{not-valid-json", encoding="utf-8")
    agg = store.aggregate_status(roles=["bob"], now=1000.0)
    assert agg["status"] == "degraded" and agg["roles"]["bob"]["state"] == "degraded"


def test_two_missed_heartbeats_hysteresis_and_two_reading_recovery(tmp_path: Path) -> None:
    store = RoleHealthStore(tmp_path / "role-health.json")
    t0 = 1_000.0
    assert store.record_heartbeat("bob", epoch=3, now=t0)["state"] == "healthy"

    r1 = store.evaluate_role("bob", now=t0 + 2 * HEARTBEAT_INTERVAL_S)
    assert r1["state"] == "degraded" and r1["reasons"] == ["missed_heartbeats"] and r1["transition"] is None

    r2 = store.evaluate_role("bob", now=t0 + 2 * HEARTBEAT_INTERVAL_S + 60.0)
    assert r2["state"] == "unhealthy" and r2["transition"] == "role_unhealthy"
    assert r2["envelope"]["code"] == "role_unhealthy" and r2["envelope"]["role"] == "bob"

    r3 = store.evaluate_role("bob", now=t0 + 2 * HEARTBEAT_INTERVAL_S + 120.0)
    assert r3["state"] == "unhealthy" and r3["transition"] is None

    t_rec = t0 + 2000.0
    rec1 = store.record_heartbeat("bob", epoch=4, now=t_rec)
    assert rec1["state"] == "unhealthy" and rec1["consecutive_healthy"] == 1 and rec1["transition"] is None

    rec2 = store.record_heartbeat("bob", epoch=4, now=t_rec + 60.0)
    assert rec2["state"] == "healthy" and rec2["transition"] == "resume"
    assert rec2["envelope"]["code"] == "resume" and rec2["envelope"]["epoch"] == 4


def test_slot_late_chain_exhausted_and_hub_probe_separation(tmp_path: Path) -> None:
    store = RoleHealthStore(tmp_path / "role-health.json")
    t0 = 5_000.0
    store.record_heartbeat("bob", epoch=1, expected_slot_at=t0, now=t0)
    t_late = t0 + SLOT_LATE_THRESHOLD_S
    s1 = store.record_heartbeat("bob", epoch=1, chain_exhausted=True, now=t_late)
    assert s1["state"] == "degraded" and set(s1["reasons"]) == {"slot_late", "chain_exhausted"}
    assert store.evaluate_role("bob", now=t_late + 10.0)["state"] == "unhealthy"

    store.record_heartbeat("pm-ud", epoch=2, now=t0)
    assert "hub_unreachable" not in store.record_hub_probe("pm-ud", ok=False, now=t0 + 60.0)["reasons"]
    assert "hub_unreachable" not in store.record_hub_probe("pm-ud", ok=False, now=t0 + 120.0)["reasons"]
    p2 = store.record_hub_probe("pm-ud", ok=False, now=t0 + 60.0 + PROBE_MIN_SEPARATION_S)
    assert "hub_unreachable" in p2["reasons"] and p2["state"] == "degraded"
    assert store.evaluate_role("pm-ud", now=t0 + 60.0 + PROBE_MIN_SEPARATION_S + 5.0)["state"] == "unhealthy"


def test_envelopes_validate_and_route_via_persona_notify(tmp_path: Path) -> None:
    alert_env = build_role_unhealthy_envelope("bob", reasons=["missed_heartbeats"], epoch=2, now=1700000000.0)
    resume_env = build_role_resume_envelope("bob", epoch=3, now=1700000600.0)
    assert rd_threads_envelope.validate(alert_env) == [] and rd_barb_notify.validate(alert_env) == []
    assert rd_threads_envelope.validate(resume_env) == [] and rd_barb_notify.validate(resume_env) == []

    sent: list[dict[str, object]] = []
    res = notify_role_transition(
        alert_env,
        config={"RD_BOB_WEBHOOK_URL": "https://example.invalid/hook", "RD_BOB_WEBHOOK_KEY": "k"},
        state_path=tmp_path / "notify-state.json",
        sender=lambda _u, _h, payload: sent.append(payload),
    )
    assert res["ok"] is True and sent[0]["code"] == "role_unhealthy" and sent[0]["sender_role"] == "bob"


def test_dbc_rejects_invalid_role_or_epoch(tmp_path: Path) -> None:
    store = RoleHealthStore(tmp_path / "role-health.json")
    with pytest.raises(ValueError, match="invalid persona role"):
        store.record_heartbeat("BAD ROLE!", epoch=1, now=100.0)
    with pytest.raises(ValueError, match="epoch"):
        store.record_heartbeat("bob", epoch=0, now=100.0)
