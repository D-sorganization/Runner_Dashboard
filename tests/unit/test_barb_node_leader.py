"""rd-barb node-leader lease + fencing primitive (#3117 slice S1, part of #3064).

Scope: a config schema naming which node leads, which node stands by, and which
nodes only ever forward, plus the lease/fencing wiring that makes "exactly one
active leader" and "a stale leader's writes are rejected" true today. This slice
does not add automatic promotion on a missed heartbeat (see docs/adr/0008 and the
#3117 handoff note for why that is deferred to a follow-up slice).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import StaleBarbLease
from staff.barb_node_leader import (
    NODE_LEADER_SCOPE,
    NodeRoleConfigError,
    assume_leadership,
    claim_for,
    current_leader,
    load_node_leader_config,
    release_leadership,
    renew_leadership,
)


def _write_config(tmp_path: Path, nodes: list[dict]) -> Path:
    path = tmp_path / "rd_barb_node_roles.json"
    path.write_text(json.dumps({"version": 1, "nodes": nodes}), encoding="utf-8")
    return path


# ── config schema ────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_shipped_config_loads_with_desk_leader_and_controltower_standby() -> None:
    config = load_node_leader_config()
    assert config.leader_name() == "DeskComputer"
    assert config.standby_names() == ("ControlTower",)
    assert config.role_of("Brick") == "forwarder"
    assert config.role_of("nonexistent") is None


@pytest.mark.unit
def test_config_rejects_zero_leaders(tmp_path: Path) -> None:
    path = _write_config(tmp_path, [{"name": "A", "role": "standby"}])
    with pytest.raises(NodeRoleConfigError, match="exactly one"):
        load_node_leader_config(path)


@pytest.mark.unit
def test_config_rejects_two_leaders(tmp_path: Path) -> None:
    path = _write_config(tmp_path, [{"name": "A", "role": "leader"}, {"name": "B", "role": "leader"}])
    with pytest.raises(NodeRoleConfigError, match="exactly one"):
        load_node_leader_config(path)


@pytest.mark.unit
def test_config_rejects_duplicate_node_names(tmp_path: Path) -> None:
    path = _write_config(tmp_path, [{"name": "A", "role": "leader"}, {"name": "A", "role": "forwarder"}])
    with pytest.raises(NodeRoleConfigError, match="duplicate"):
        load_node_leader_config(path)


@pytest.mark.unit
def test_config_rejects_unknown_role(tmp_path: Path) -> None:
    path = _write_config(tmp_path, [{"name": "A", "role": "leader"}, {"name": "B", "role": "overlord"}])
    with pytest.raises(NodeRoleConfigError, match="role"):
        load_node_leader_config(path)


@pytest.mark.unit
def test_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NodeRoleConfigError):
        load_node_leader_config(tmp_path / "missing.json")


# ── lease wrapper: exactly one active leader ─────────────────────────────────
@pytest.mark.unit
def test_first_node_to_assume_leadership_acquires_it(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    result = assume_leadership(store, node="DeskComputer")
    assert result["acquired"] is True
    assert result["lease"]["scope"] == NODE_LEADER_SCOPE
    assert result["lease"]["holder"] == "DeskComputer"
    assert result["lease"]["epoch"] == 1


@pytest.mark.unit
def test_a_second_node_cannot_take_leadership_while_the_first_is_live(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    assume_leadership(store, node="DeskComputer")
    conflict = assume_leadership(store, node="ControlTower")
    assert conflict["acquired"] is False
    assert conflict["conflict"] is True
    assert current_leader(store)["holder"] == "DeskComputer"


@pytest.mark.unit
def test_a_forced_takeover_bumps_the_epoch_and_changes_the_holder(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    assume_leadership(store, node="DeskComputer")
    takeover = assume_leadership(store, node="ControlTower", force=True)
    assert takeover["acquired"] is True
    assert takeover["lease"]["holder"] == "ControlTower"
    assert takeover["lease"]["epoch"] == 2
    assert current_leader(store)["holder"] == "ControlTower"


@pytest.mark.unit
def test_renew_extends_the_live_leader_without_bumping_the_epoch(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    acquired = assume_leadership(store, node="DeskComputer")
    renewed = renew_leadership(store, node="DeskComputer", epoch=acquired["lease"]["epoch"])
    assert renewed["renewed"] is True
    assert renewed["lease"]["epoch"] == acquired["lease"]["epoch"]


@pytest.mark.unit
def test_release_clears_the_leader_so_another_node_may_acquire(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    assume_leadership(store, node="DeskComputer")
    released = release_leadership(store, node="DeskComputer")
    assert released["released"] is True
    assert current_leader(store) is None
    acquired = assume_leadership(store, node="ControlTower")
    assert acquired["acquired"] is True


@pytest.mark.unit
def test_current_leader_is_none_when_nobody_holds_the_scope(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    assert current_leader(store) is None


# ── fencing: a stale leader's writes are rejected ────────────────────────────
@pytest.mark.unit
def test_fencing_accepts_a_claim_at_the_current_epoch(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    acquired = assume_leadership(store, node="DeskComputer")
    epoch = acquired["lease"]["epoch"]
    with store.fence(claim_for("DeskComputer", epoch)) as fenced:
        assert fenced["epoch"] == epoch


@pytest.mark.unit
def test_fencing_rejects_a_claim_from_a_node_the_lease_no_longer_names(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    acquired = assume_leadership(store, node="DeskComputer")
    stale_epoch = acquired["lease"]["epoch"]
    assume_leadership(store, node="ControlTower", force=True)  # Desk's heartbeat lapsed; CT took over
    with pytest.raises(StaleBarbLease, match="holder_mismatch"):
        with store.fence(claim_for("DeskComputer", stale_epoch)):
            raise AssertionError("must not execute: the fence must reject before the body runs")


@pytest.mark.unit
def test_fencing_rejects_a_stale_epoch_from_the_same_node_after_it_expired_and_re_acquired(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb.db")
    t0 = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
    acquired = assume_leadership(store, node="DeskComputer", ttl_seconds=60, now=t0)
    stale_epoch = acquired["lease"]["epoch"]
    later = t0 + timedelta(seconds=120)  # the lease expired; Desk itself restarts and re-acquires
    reacquired = assume_leadership(store, node="DeskComputer", now=later)
    assert reacquired["lease"]["epoch"] != stale_epoch
    with pytest.raises(StaleBarbLease, match="stale_epoch"):
        with store.fence(claim_for("DeskComputer", stale_epoch), now=later):
            raise AssertionError("must not execute: the fence must reject before the body runs")
