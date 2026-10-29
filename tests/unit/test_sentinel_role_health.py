"""Unit tests for node-c sentinel per-role health probe and wake envelopes (#3295 S6)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "backend"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from staff.sentinel_role_health import (  # noqa: E402
    BACKSTOP_INTERVAL_S,
    PROBE_MIN_SEPARATION_S,
    SentinelRoleHealthMonitor,
    build_sentinel_wake_envelope,
    dispatch_wake_envelopes,
    probe_roles_health,
)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _probe(reachable: bool = True, status: str = "healthy", **roles: dict[str, Any]) -> dict[str, Any]:
    return {"reachable": reachable, "classification": "ok", "detail": status, "status": status, "roles": roles}


def test_probe_roles_health_classifies_transport_http_invalid_and_ok_and_never_leaks_token() -> None:
    secret = "secret-bearer-token-xyz-987"  # pragma: allowlist secret
    seen_auth: list[str | None] = []

    def down_opener(req: Any, timeout: float) -> Any:
        seen_auth.append(req.get_header("Authorization"))
        raise URLError(f"connection refused with {secret}")

    res_down = probe_roles_health("http://hub.example.test:8321", bearer_token=secret, opener=down_opener)
    assert (res_down["reachable"], res_down["classification"]) == (False, "transport_down")
    assert seen_auth == [f"Bearer {secret}"]
    assert secret not in json.dumps(res_down)

    def http_err(req: Any, timeout: float) -> Any:
        raise HTTPError(req.full_url, 503, f"unavailable {secret}", hdrs=None, fp=io.BytesIO(b""))  # type: ignore[arg-type]

    res_http = probe_roles_health("http://hub.example.test:8321", bearer_token=secret, opener=http_err)
    assert (res_http["reachable"], res_http["classification"]) == (False, "http_error")
    assert secret not in json.dumps(res_http)

    res_bad = probe_roles_health("http://hub.example.test:8321", opener=lambda r, t: _FakeResponse(b"not-json"))
    assert (res_bad["reachable"], res_bad["classification"], res_bad["status"]) == (True, "invalid_payload", "degraded")

    payload = {
        "status": "healthy",
        "roles": {
            "bob": {"state": "healthy", "reasons": [], "epoch": 3},
            "rd-barb": {"state": "mystery_state", "reasons": [], "epoch": 2},
        },
    }
    raw = json.dumps(payload).encode()
    res_ok = probe_roles_health("http://hub.example.test:8321", opener=lambda r, t: _FakeResponse(raw))
    assert (res_ok["reachable"], res_ok["classification"], res_ok["status"]) == (True, "ok", "degraded")
    assert res_ok["roles"]["bob"]["state"] == "healthy"
    assert res_ok["roles"]["rd-barb"]["state"] == "degraded"
    assert res_ok["roles"]["rd-barb"]["reasons"] == ["unknown_health"]


def test_unreachable_hub_requires_two_probes_ten_minutes_apart_and_wakes_grok_once(tmp_path: Path) -> None:
    monitor = SentinelRoleHealthMonitor(tmp_path / "sentinel-role-health.json")
    down = _probe(reachable=False, status="unhealthy")

    first = monitor.evaluate_probe_result(down, tracked_roles=["bob", "rd-barb"], now=1000.0)
    assert (first["status"], first["envelopes"]) == ("degraded", [])

    t_early = 1000.0 + PROBE_MIN_SEPARATION_S - 1.0
    assert monitor.evaluate_probe_result(down, tracked_roles=["bob", "rd-barb"], now=t_early)["envelopes"] == []

    t_ok = 1000.0 + PROBE_MIN_SEPARATION_S
    confirmed = monitor.evaluate_probe_result(down, tracked_roles=["bob", "rd-barb"], now=t_ok)
    assert confirmed["status"] == "unhealthy"
    by_role = {env["role"]: env for env in confirmed["envelopes"]}
    assert set(by_role) == {"bob", "rd-barb"}
    assert by_role["bob"]["code"] == "role_unhealthy"
    assert by_role["bob"]["decision_authority"] == "grok"
    assert by_role["bob"]["reasons"] == ["hub_unreachable"]
    t_next = 1000.0 + 2 * PROBE_MIN_SEPARATION_S
    assert monitor.evaluate_probe_result(down, tracked_roles=["bob", "rd-barb"], now=t_next)["envelopes"] == []


def test_reachable_hub_applies_two_reading_hysteresis_with_rd_authority_and_rd_only_failback(tmp_path: Path) -> None:
    monitor = SentinelRoleHealthMonitor(tmp_path / "sentinel-role-health.json")
    unhealthy = _probe(
        status="unhealthy", bob={"state": "unhealthy", "reasons": ["missed_heartbeats", "slot_late"], "epoch": 4}
    )
    r1 = monitor.evaluate_probe_result(unhealthy, tracked_roles=["bob"], now=1000.0)
    assert (r1["roles"]["bob"]["sentinel_state"], r1["envelopes"]) == ("degraded", [])

    r2 = monitor.evaluate_probe_result(unhealthy, tracked_roles=["bob"], now=1600.0)
    assert r2["roles"]["bob"]["sentinel_state"] == "unhealthy"
    env = r2["envelopes"][0]
    assert (env["role"], env["code"], env["decision_authority"], env["epoch"]) == ("bob", "role_unhealthy", "rd", 4)
    assert env["reasons"] == ["missed_heartbeats", "slot_late"]
    assert monitor.evaluate_probe_result(unhealthy, tracked_roles=["bob"], now=2200.0)["envelopes"] == []

    healthy = _probe(status="healthy", bob={"state": "healthy", "reasons": [], "epoch": 5})
    h1 = monitor.evaluate_probe_result(healthy, tracked_roles=["bob"], now=2800.0)
    assert (h1["roles"]["bob"]["sentinel_state"], h1["envelopes"]) == ("unhealthy", [])

    h2 = monitor.evaluate_probe_result(healthy, tracked_roles=["bob"], now=3400.0)
    assert (h2["roles"]["bob"]["sentinel_state"], h2["envelopes"]) == ("healthy", [])

    monitor.evaluate_probe_result(unhealthy, tracked_roles=["bob"], now=4000.0)
    assert len(monitor.evaluate_probe_result(unhealthy, tracked_roles=["bob"], now=4600.0)["envelopes"]) == 1


def test_corrupt_or_missing_state_fails_closed_without_alert_storm(tmp_path: Path) -> None:
    state_file = tmp_path / "sentinel-role-health.json"
    state_file.write_text("{corrupt json", encoding="utf-8")
    monitor = SentinelRoleHealthMonitor(state_file)
    unhealthy = _probe(status="unhealthy", bob={"state": "unhealthy", "reasons": ["chain_exhausted"], "epoch": 2})
    first = monitor.evaluate_probe_result(unhealthy, tracked_roles=["bob"], now=500.0)
    assert (first["roles"]["bob"]["sentinel_state"], first["envelopes"]) == ("degraded", [])


def test_four_hour_backstop_schedule_and_wake_dispatch_isolation(tmp_path: Path) -> None:
    monitor = SentinelRoleHealthMonitor(tmp_path / "sentinel-role-health.json")
    assert monitor.is_backstop_due(now=1000.0) is True
    monitor.record_backstop_check(now=1000.0)
    assert monitor.is_backstop_due(now=1000.0 + BACKSTOP_INTERVAL_S - 1.0) is False
    assert monitor.is_backstop_due(now=1000.0 + BACKSTOP_INTERVAL_S) is True

    env_bob = build_sentinel_wake_envelope(
        "bob", reasons=["hub_unreachable"], epoch=2, decision_authority="grok", now=1000.0
    )
    env_barb = build_sentinel_wake_envelope(
        "rd-barb", reasons=["missed_heartbeats"], epoch=3, decision_authority="rd", now=1000.0
    )
    delivered: list[str] = []

    def flaky_sender(url: str, headers: Any, payload: dict[str, Any]) -> None:
        if payload.get("sender_role") == "bob":
            raise RuntimeError("webhook timeout")
        delivered.append(str(payload.get("sender_role")))

    cfg = {"RD_BARB_WEBHOOK_URL": "https://webhook.example.test/hook", "RD_BARB_WEBHOOK_KEY": "test-key"}
    outcomes = dispatch_wake_envelopes(
        [env_bob, env_barb], config=cfg, notify_state_path=tmp_path / "notify-state.json", sender=flaky_sender
    )
    assert [o["ok"] for o in outcomes] == [False, True]
    assert delivered == ["rd-barb"]

    with pytest.raises(ValueError, match="decision_authority"):
        build_sentinel_wake_envelope("bob", reasons=["hub_unreachable"], decision_authority="invalid")
