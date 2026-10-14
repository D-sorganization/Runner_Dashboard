"""Tests for fleet capacity reservation and budget governance (BR-17, #1810)."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from staff.capacity_reservation import (
    CapacityLimits,
    CapacityReservationStore,
    get_capacity_reservation_store,
    reset_capacity_reservation_store,
)


def test_concurrent_requests_cannot_exceed_configured_slots(tmp_path: Path) -> None:
    """Concurrent requests from two or more nodes/threads cannot exceed configured slots."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(
        max_concurrent_slots=3,
        repair_reserved_slots=0,
        max_daily_budget_usd=100.0,
    )

    def do_reserve(op_id: str, node: str):
        # Fresh store instance per thread pointing to the same db file to simulate nodes
        s = CapacityReservationStore(path=db_file, limits=limits)
        return s.reserve(
            operation_id=op_id,
            role="worker",
            provider="anthropic",
            slots=1,
            estimated_spend_usd=1.0,
            priority=0,
            node=node,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(do_reserve, f"op-{i}", f"node-{i % 2}") for i in range(6)]
        results = [f.result() for f in futures]

    admitted = [r for r in results if r.admitted]
    refused = [r for r in results if not r.admitted]

    assert len(admitted) == 3
    assert len(refused) == 3
    for ref in refused:
        assert ref.refusal_reason == "capacity_exhausted"
        assert ref.queue_position is not None
        assert ref.next_eligible_time_iso is not None
        assert "Refused: Fleet capacity exhausted" in ref.explanation


def test_high_priority_repair_work_gets_capacity_without_cancelling_running_ci(tmp_path: Path) -> None:
    """Repair work with priority > 0 uses reserved repair slots without cancelling PR CI."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(
        max_concurrent_slots=4,
        repair_reserved_slots=1,  # 3 standard slots, 1 repair headroom
        max_daily_budget_usd=100.0,
    )
    store = CapacityReservationStore(path=db_file, limits=limits)

    # Admit 3 normal operations (filling normal capacity)
    for i in range(3):
        res = store.reserve(f"normal-{i}", "worker", "anthropic", slots=1, priority=0)
        assert res.admitted is True

    # 4th normal operation should be refused because normal cap (4 - 1 = 3) is full
    res_normal_4 = store.reserve("normal-3", "worker", "anthropic", slots=1, priority=0)
    assert res_normal_4.admitted is False
    assert res_normal_4.refusal_reason == "capacity_exhausted"

    # High priority repair operation should be admitted into repair headroom
    res_repair = store.reserve("repair-1", "barb", "anthropic", slots=1, priority=1)
    assert res_repair.admitted is True
    assert res_repair.reservation is not None
    assert res_repair.reservation.priority == 1


def test_settle_releases_slot_without_double_spend(tmp_path: Path) -> None:
    """Settling actual usage frees active slot and records actual spend without double counting."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(
        max_concurrent_slots=1,
        repair_reserved_slots=0,
        max_daily_budget_usd=10.0,
    )
    store = CapacityReservationStore(path=db_file, limits=limits)

    # Reserve slot
    r1 = store.reserve("op-settle-1", "worker", "anthropic", slots=1, estimated_spend_usd=3.0)
    assert r1.admitted is True

    # Another reserve is blocked while first is active
    r2_blocked = store.reserve("op-settle-2", "worker", "anthropic", slots=1)
    assert r2_blocked.admitted is False

    # Settle first reservation
    settled = store.settle("op-settle-1", actual_spend_usd=2.50)
    assert settled is not None
    assert settled.status == "settled"
    assert settled.actual_spend_usd == 2.50

    # Now second operation can reserve slot
    r2_admitted = store.reserve("op-settle-2", "worker", "anthropic", slots=1, estimated_spend_usd=1.0)
    assert r2_admitted.admitted is True


def test_node_loss_reconciliation(tmp_path: Path) -> None:
    """Node loss releases reservations without double-spend."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(max_concurrent_slots=2, repair_reserved_slots=0)
    store = CapacityReservationStore(path=db_file, limits=limits)

    store.reserve("op-node1-a", "worker", "anthropic", slots=1, node="node-lost")
    store.reserve("op-node1-b", "worker", "anthropic", slots=1, node="node-lost")

    # All slots taken
    blocked = store.reserve("op-node2", "worker", "anthropic", slots=1, node="node-live")
    assert blocked.admitted is False

    # Node lost: reconcile
    orphaned = store.reconcile_node_loss("node-lost")
    assert set(orphaned) == {"op-node1-a", "op-node1-b"}

    # Slots freed, live node can now reserve
    live = store.reserve("op-node2", "worker", "anthropic", slots=1, node="node-live")
    assert live.admitted is True


def test_unknown_quota_policy(tmp_path: Path) -> None:
    """Unknown quota follows explicit policy (refuse by default)."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(unknown_quota_policy="refuse")
    store = CapacityReservationStore(path=db_file, limits=limits)

    res = store.reserve("op-quota-unknown", "worker", "anthropic", quota_known=False)
    assert res.admitted is False
    assert "quota_unknown" in res.refusal_reason
    assert "Quota for provider 'anthropic' is unknown" in res.explanation

    # Allow policy
    allow_store = CapacityReservationStore(
        path=db_file,
        limits=CapacityLimits(unknown_quota_policy="allow"),
    )
    res_allow = allow_store.reserve("op-quota-allow", "worker", "anthropic", quota_known=False)
    assert res_allow.admitted is True


def test_subscription_effort_separate_from_billed_spend(tmp_path: Path) -> None:
    """Notional subscription effort does not deplete billed API spend budget."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(
        max_concurrent_slots=10,
        repair_reserved_slots=0,
        max_daily_budget_usd=5.0,
    )
    store = CapacityReservationStore(path=db_file, limits=limits)

    # Subscription run with high notional effort
    sub = store.reserve("op-sub", "worker", "chatgpt_subscription", estimated_spend_usd=20.0, is_subscription=True)
    assert sub.admitted is True

    # Billed run fits under $5.00 limit
    billed_ok = store.reserve("op-billed-1", "worker", "anthropic", estimated_spend_usd=4.0, is_subscription=False)
    assert billed_ok.admitted is True

    # Another billed run exceeds $5.00 limit
    billed_fail = store.reserve("op-billed-2", "worker", "anthropic", estimated_spend_usd=2.0, is_subscription=False)
    assert billed_fail.admitted is False
    assert billed_fail.refusal_reason == "daily_budget_exhausted"


def test_lease_sweep_and_expiry(tmp_path: Path) -> None:
    """Expired leases are swept and free slots."""
    db_file = tmp_path / "capacity.sqlite3"
    limits = CapacityLimits(max_concurrent_slots=1, repair_reserved_slots=0)
    store = CapacityReservationStore(path=db_file, limits=limits)

    # Reserve with TTL of -10 seconds (already expired)
    r = store.reserve("op-exp", "worker", "anthropic", ttl_seconds=-10)
    assert r.admitted is True

    # Sweeping expires it
    expired = store.sweep_expired()
    assert "op-exp" in expired

    # Slot is immediately free for next run
    r2 = store.reserve("op-fresh", "worker", "anthropic")
    assert r2.admitted is True


def test_idempotent_reserve_and_release(tmp_path: Path) -> None:
    """Same operation_id re-reserving returns existing reservation idempotently."""
    db_file = tmp_path / "capacity.sqlite3"
    store = CapacityReservationStore(path=db_file)

    r1 = store.reserve("op-idem", "worker", "anthropic")
    assert r1.admitted is True

    r2 = store.reserve("op-idem", "worker", "anthropic")
    assert r2.admitted is True
    assert r2.reservation is not None
    assert r2.reservation.operation_id == "op-idem"

    # Release
    released = store.release("op-idem")
    assert released is True

    # Re-release returns False
    assert store.release("op-idem") is False


def test_get_and_reset_capacity_reservation_store(tmp_path: Path) -> None:
    """Singleton accessor returns consistent instance and can be reset."""
    db_file = tmp_path / "singleton_cap.sqlite3"
    reset_capacity_reservation_store()
    s1 = get_capacity_reservation_store(path=db_file)
    s2 = get_capacity_reservation_store()
    assert s1 is s2
    reset_capacity_reservation_store()
