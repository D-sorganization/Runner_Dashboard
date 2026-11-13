"""Client side of the Barb lease-epoch fence (#2734): auto headers and the lost-seat error."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fleet_client import FleetAPIError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401 - pytest fixtures

P = "/api/v1/staff/barb"
SCOPE = "barb:pr-queue"
LEASE = {"scope": SCOPE, "holder": "agent-alpha", "epoch": 3}
STALE = {
    "detail": {
        "error": "stale_barb_lease",
        "reason": "holder_mismatch",
        "scope": SCOPE,
        "current_epoch": 4,
        "claimed_epoch": 3,
        "holder": "op-1",
    }
}


def _client(fake_api: Any) -> FleetClient:  # noqa: F811
    return FleetClient(base_url=fake_api.url, token="tok")


def _seated(fake_api: Any) -> FleetClient:  # noqa: F811
    fake_api.respond("POST", f"{P}/leases/checkout", 200, {"ok": True, "lease": LEASE})
    c = _client(fake_api)
    c.barb_seat_checkout(SCOPE)
    return c


def _barb_headers(req: Any) -> dict[str, str]:
    return {k: v for k, v in req.headers.items() if k.startswith("x-barb-")}


def test_no_seat_means_no_claim_headers(fake_api: Any) -> None:  # noqa: F811
    _client(fake_api).memory_append("session_digest", "dashboard", {"summary": "s"})
    assert _barb_headers(fake_api.last) == {}


def test_checkout_caches_seat_and_writes_carry_it(fake_api: Any) -> None:  # noqa: F811
    c = _seated(fake_api)
    assert _barb_headers(fake_api.last) == {}  # lease management itself never claims
    c.memory_append("session_digest", "dashboard", {"summary": "s"})
    assert _barb_headers(fake_api.last) == {
        "x-barb-scope": SCOPE,
        "x-barb-lease-epoch": "3",
        "x-barb-holder": "agent-alpha",
    }
    c.dispatch("ad-hoc", prompt="hi")
    assert _barb_headers(fake_api.last)["x-barb-lease-epoch"] == "3"
    c.set_directives([{"text": "x", "priority": 2, "repo": "*"}])
    assert _barb_headers(fake_api.last)["x-barb-scope"] == SCOPE
    c.barb_seat_turnover(SCOPE, "done")
    assert _barb_headers(fake_api.last)["x-barb-scope"] == SCOPE


def test_reads_and_lease_calls_do_not_claim(fake_api: Any) -> None:  # noqa: F811
    c = _seated(fake_api)
    c.barb_seat_status()
    assert _barb_headers(fake_api.last) == {}
    c.barb_seat_heartbeat(SCOPE, 3)
    assert _barb_headers(fake_api.last) == {}


def test_heartbeat_refreshes_cached_epoch(fake_api: Any) -> None:  # noqa: F811
    c = _seated(fake_api)
    fake_api.respond("POST", f"{P}/leases/heartbeat", 200, {"ok": True, "lease": {**LEASE, "epoch": 5}})
    c.barb_seat_heartbeat(SCOPE, 3)
    c.memory_append("session_digest", "dashboard", {"summary": "s"})
    assert _barb_headers(fake_api.last)["x-barb-lease-epoch"] == "5"


def test_release_forgets_the_seat(fake_api: Any) -> None:  # noqa: F811
    c = _seated(fake_api)
    fake_api.respond("POST", f"{P}/leases/release", 200, {"ok": True, "released": True})
    c.barb_seat_release(SCOPE, 3)
    c.memory_append("session_digest", "dashboard", {"summary": "s"})
    assert _barb_headers(fake_api.last) == {}


def test_seat_survives_a_new_client_process(fake_api: Any) -> None:  # noqa: F811
    _seated(fake_api)
    _client(fake_api).memory_append("session_digest", "dashboard", {"summary": "s"})  # fresh object, same seat file
    assert _barb_headers(fake_api.last)["x-barb-lease-epoch"] == "3"


def test_unwritable_seat_file_is_harmless(
    fake_api: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,  # noqa: F811
) -> None:
    monkeypatch.setenv("FLEET_BARB_SEAT_FILE", str(tmp_path))  # a directory: every read and write fails
    c = _seated(fake_api)
    c.memory_append("session_digest", "dashboard", {"summary": "s"})
    assert _barb_headers(fake_api.last)["x-barb-lease-epoch"] == "3"  # still cached in memory


def test_stale_409_is_a_clear_lost_seat_error_and_is_not_retried(fake_api: Any) -> None:  # noqa: F811
    c = _seated(fake_api)
    fake_api.respond("PUT", "/api/priorities/directives", 409, STALE)  # PUT would be retried only on 5xx
    fake_api.respond("POST", "/api/v1/memory", 409, STALE)
    before = len(fake_api.requests)
    with pytest.raises(FleetAPIError) as err:
        c.memory_append("session_digest", "dashboard", {"summary": "s"})
    assert err.value.status == 409
    msg = err.value.body["detail"]
    assert "You lost the Barb seat barb:pr-queue" in msg
    assert "epoch 3 -> 4" in msg
    assert "op-1" in msg
    assert "re-checkout" in msg.lower()
    assert "will not be retried" in msg
    assert len(fake_api.requests) == before + 1  # exactly one attempt
    assert err.value.to_envelope()["message"] == msg
    with pytest.raises(FleetAPIError):
        c.set_directives([{"text": "x", "priority": 2, "repo": "*"}])
    assert len(fake_api.requests) == before + 2


def test_other_409s_are_untouched(fake_api: Any) -> None:  # noqa: F811
    c = _seated(fake_api)
    fake_api.respond("POST", "/api/v1/memory", 409, {"detail": "version conflict"})
    with pytest.raises(FleetAPIError) as err:
        c.memory_append("session_digest", "dashboard", {"summary": "s"})
    assert err.value.body == {"detail": "version conflict"}
