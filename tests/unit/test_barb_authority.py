"""Authority configuration and the epoch-fenced AuthorityClient (#1800, BR-06)."""

from __future__ import annotations

from typing import Any

import pytest
from staff.barb_authority import (
    AuthorityClient,
    AuthorityConfig,
    AuthorityEpochMismatch,
    AuthorityRejected,
    AuthorityRole,
    AuthorityUnavailable,
    DeliveryOutcome,
    registry_hub,
)


@pytest.mark.unit
def test_unset_means_legacy_single_node() -> None:
    cfg = AuthorityConfig.from_env({}, local_node="nodeA")
    assert cfg.role is AuthorityRole.UNSET and cfg.epoch == 0
    assert not cfg.is_authority and not cfg.is_replica


@pytest.mark.unit
def test_authority_and_replica_roles() -> None:
    env = {"BARB_AUTHORITY_NODE": "ControlTower", "BARB_AUTHORITY_URL": "http://ct:8321/", "BARB_AUTHORITY_EPOCH": "4"}
    auth = AuthorityConfig.from_env(env, local_node="controltower")
    assert auth.role is AuthorityRole.AUTHORITY and auth.epoch == 4 and auth.url == "http://ct:8321"
    rep = AuthorityConfig.from_env(env, local_node="DeskComputer")
    assert rep.role is AuthorityRole.REPLICA and rep.url == "http://ct:8321"


@pytest.mark.unit
@pytest.mark.parametrize(
    "env",
    [
        {"BARB_AUTHORITY_NODE": "A", "BARB_AUTHORITY_EPOCH": "0"},
        {"BARB_AUTHORITY_NODE": "A", "BARB_AUTHORITY_EPOCH": "x"},
        {"BARB_AUTHORITY_NODE": "A", "BARB_AUTHORITY_URL": "ftp://a"},
        {"BARB_AUTHORITY_URL": "http://a:1"},  # a url without a node name is ambiguous
    ],
)
def test_invalid_config_is_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        AuthorityConfig.from_env(env, local_node="B")


@pytest.mark.unit
def test_replica_requires_a_url() -> None:
    with pytest.raises(ValueError):
        AuthorityConfig.from_env({"BARB_AUTHORITY_NODE": "A"}, local_node="B")


@pytest.mark.unit
def test_registry_hub_default_and_manifest_key_override() -> None:
    machines = [
        {"name": "DeskComputer", "role": "worker", "dashboard_url": "http://d:8321"},
        {"name": "ControlTower", "role": "hub", "dashboard_url": "http://ct:8321"},
    ]
    assert registry_hub({"machines": machines}) == ("ControlTower", "http://ct:8321")
    assert registry_hub({"machines": machines, "barb_authority": "DeskComputer"}) == ("DeskComputer", "http://d:8321")
    assert registry_hub({"machines": []}) is None


class _Transport:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, body, headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _client(t: _Transport, epoch: int = 3) -> AuthorityClient:
    cfg = AuthorityConfig.from_env(
        {"BARB_AUTHORITY_NODE": "CT", "BARB_AUTHORITY_URL": "http://ct:8321", "BARB_AUTHORITY_EPOCH": str(epoch)},
        local_node="other",
    )
    return AuthorityClient(cfg, t, headers={"X-Requested-With": "XMLHttpRequest"})


@pytest.mark.unit
def test_post_carries_epoch_version_and_key_and_maps_outcomes() -> None:
    t = _Transport((200, {"outcome": "applied"}), (200, {"outcome": "duplicate"}), (409, {"code": "stale_version"}))
    c = _client(t)
    assert c.post("run_card", "thr", "run-1", 2, {"status": "running"}) is DeliveryOutcome.DELIVERED
    url, body, headers = t.calls[0]
    assert url == "http://ct:8321/api/v1/staff/threads/thr/relay-card"
    assert body["version"] == 2 and body["epoch"] == 3 and body["run_id"] == "run-1"
    assert headers["Idempotency-Key"] == "run_card:thr:run-1:2"
    assert c.post("run_card", "thr", "run-1", 2, {"status": "running"}) is DeliveryOutcome.DELIVERED
    assert c.post("run_card", "thr", "run-1", 1, {"status": "queued"}) is DeliveryOutcome.STALE


@pytest.mark.unit
def test_failures_are_classified() -> None:
    t = _Transport(
        ConnectionError("down"),
        (503, {}),
        (429, {}),
        (409, {"code": "stale_authority_epoch"}),
        (422, {"detail": "bad"}),
    )
    c = _client(t)
    for _ in range(3):
        with pytest.raises(AuthorityUnavailable):
            c.post("run_card", "thr", "run-1", 1, {})
    with pytest.raises(AuthorityEpochMismatch):
        c.post("run_card", "thr", "run-1", 1, {})
    with pytest.raises(AuthorityRejected):
        c.post("run_card", "thr", "run-1", 1, {})


@pytest.mark.unit
def test_client_preconditions() -> None:
    c = _client(_Transport())
    with pytest.raises(ValueError):
        c.post("run_card", "thr", "run-1", 0, {})
    with pytest.raises(ValueError):
        c.post("unknown_kind", "thr", "run-1", 1, {})
    unset = AuthorityConfig.from_env({}, local_node="x")
    with pytest.raises(ValueError):
        AuthorityClient(unset, _Transport())
