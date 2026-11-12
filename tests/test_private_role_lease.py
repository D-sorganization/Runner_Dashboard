"""Per-role leader lease + fencing (issue #3098, private-roles slice 5).

Exercises ``private_role_lease`` with two hosts sharing one on-disk lease
store (LoD: only the module's public accessors, never the raw JSON file)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_role_lease as prl  # noqa: E402
from staff.holds import Hold, HoldsList  # noqa: E402

ROLE_ID = "work-context-pilot"
GROK_HOST = "grok-bot-host"
RD_HOST = "rd-host"
TTL = 30.0
NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
AFTER_EXPIRY = NOW + timedelta(seconds=TTL + 1)


def _acquire(host: str, now: datetime, store_dir: Path) -> prl.PrivateRoleLease | prl.LeaseDenied:
    return prl.acquire_or_renew_lease(role_id=ROLE_ID, host=host, ttl_seconds=TTL, store_dir=store_dir, now=now)


def _fence(
    host: str, token: int, action: prl.MutationAction, store_dir: Path, now: datetime
) -> prl.PrivateRoleLease | prl.LeaseDenied:
    return prl.fence_mutation(
        role_id=ROLE_ID, host=host, fencing_token=token, action=action, store_dir=store_dir, now=now
    )


def _claim(host: str, token: int, at: datetime) -> prl.LeaderClaim:
    return prl.LeaderClaim(role_id=ROLE_ID, host=host, fencing_token=token, asserted_at=at.isoformat())


def test_lease_acquire_then_renew_by_same_holder(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    lease = _acquire(GROK_HOST, NOW, store_dir)
    assert isinstance(lease, prl.PrivateRoleLease)
    assert lease.holder == GROK_HOST
    assert lease.fencing_token == 1

    renewed = _acquire(GROK_HOST, NOW + timedelta(seconds=5), store_dir)
    assert isinstance(renewed, prl.PrivateRoleLease)
    assert renewed.fencing_token == lease.fencing_token  # same holder: no new token minted
    assert renewed.expires_at > lease.expires_at


def test_lease_denied_to_second_host_while_active(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    _acquire(GROK_HOST, NOW, store_dir)
    denied = _acquire(RD_HOST, NOW + timedelta(seconds=1), store_dir)
    assert isinstance(denied, prl.LeaseDenied)
    assert denied.reason == "held-by-other-host"


def test_lease_expires_and_is_reacquirable_by_another_host(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    first = _acquire(GROK_HOST, NOW, store_dir)
    assert isinstance(first, prl.PrivateRoleLease)

    second = _acquire(RD_HOST, AFTER_EXPIRY, store_dir)
    assert isinstance(second, prl.PrivateRoleLease)
    assert second.holder == RD_HOST
    assert second.fencing_token == first.fencing_token + 1  # monotonic across hosts


def test_stale_token_rejected_by_fence_mutation(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    lease = _acquire(GROK_HOST, NOW, store_dir)
    assert isinstance(lease, prl.PrivateRoleLease)
    stale_token = lease.fencing_token

    reacquired = _acquire(RD_HOST, AFTER_EXPIRY, store_dir)  # mints a new token
    assert isinstance(reacquired, prl.PrivateRoleLease)

    result = _fence(GROK_HOST, stale_token, "board_post", store_dir, AFTER_EXPIRY)
    assert isinstance(result, prl.LeaseDenied)
    assert result.reason == "stale-token"


def test_follower_mutation_refused(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    lease = _acquire(GROK_HOST, NOW, store_dir)
    assert isinstance(lease, prl.PrivateRoleLease)

    # RD_HOST never held the lease: it is a follower and is read-only.
    result = _fence(RD_HOST, lease.fencing_token, "thread_reply", store_dir, NOW)
    assert isinstance(result, prl.LeaseDenied)
    assert result.reason == "not-leader"


def test_fence_mutation_allows_current_leader_with_current_token(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    lease = _acquire(GROK_HOST, NOW, store_dir)
    assert isinstance(lease, prl.PrivateRoleLease)
    result = _fence(GROK_HOST, lease.fencing_token, "pr_action", store_dir, NOW)
    assert isinstance(result, prl.PrivateRoleLease)


def test_revocation_rollback_rule(tmp_path: Path) -> None:
    """Issue #3098 acceptance: revocation during a write-in-progress."""
    store_dir = tmp_path / "leases"
    lease = _acquire(GROK_HOST, NOW, store_dir)
    assert isinstance(lease, prl.PrivateRoleLease)
    in_flight_token = lease.fencing_token

    still_current = prl.complete_write_in_progress(
        role_id=ROLE_ID, claimed_token=in_flight_token, idempotent=False, store_dir=store_dir, now=NOW
    )
    assert still_current == "completed"

    prl.revoke_lease(role_id=ROLE_ID, host=GROK_HOST, store_dir=store_dir, now=NOW)
    reacquired = _acquire(RD_HOST, NOW, store_dir)
    assert isinstance(reacquired, prl.PrivateRoleLease)
    assert reacquired.fencing_token != in_flight_token

    idempotent_outcome = prl.complete_write_in_progress(
        role_id=ROLE_ID, claimed_token=in_flight_token, idempotent=True, store_dir=store_dir, now=NOW
    )
    assert idempotent_outcome == "completed"

    non_idempotent_outcome = prl.complete_write_in_progress(
        role_id=ROLE_ID, claimed_token=in_flight_token, idempotent=False, store_dir=store_dir, now=NOW
    )
    assert non_idempotent_outcome == "rolled-back"


def test_split_brain_halts_mutations_and_emits_one_conflict_notice(tmp_path: Path) -> None:
    """Issue #3098 acceptance: split-brain halts mutations and emits one conflict notice."""
    store_dir = tmp_path / "leases"
    notices: list[prl.LeaseConflict] = []
    claims = [_claim(GROK_HOST, 1, NOW), _claim(RD_HOST, 1, NOW + timedelta(seconds=1))]

    conflict = prl.reconcile_claims(role_id=ROLE_ID, claims=claims, store_dir=store_dir, now=NOW, notify=notices.append)
    assert isinstance(conflict, prl.LeaseConflict)
    assert set(conflict.hosts) == {GROK_HOST, RD_HOST}
    assert notices == [conflict]

    # Reconciling the same open conflict again must not re-notify.
    prl.reconcile_claims(role_id=ROLE_ID, claims=claims, store_dir=store_dir, now=NOW, notify=notices.append)
    assert len(notices) == 1

    result = _fence(GROK_HOST, 1, "context_write", store_dir, NOW)
    assert isinstance(result, prl.LeaseDenied)
    assert result.reason == "split-brain"


def test_reconcile_claims_with_single_host_is_not_a_conflict(tmp_path: Path) -> None:
    store_dir = tmp_path / "leases"
    notices: list[prl.LeaseConflict] = []
    result = prl.reconcile_claims(
        role_id=ROLE_ID, claims=[_claim(GROK_HOST, 1, NOW)], store_dir=store_dir, now=NOW, notify=notices.append
    )
    assert result is None
    assert notices == []


def test_holds_are_untouched_by_any_lease_transition(tmp_path: Path) -> None:
    """Issue #3098 acceptance: holds are immutable, never read or written by a lease flip."""
    holds_list = HoldsList(path=tmp_path / "holds.json", roles_loader=lambda: {})
    before = holds_list.replace([Hold(id="hold-no-flip", text="never lift a hold on a lease flip").to_dict()])
    before_snapshot = [h.to_dict() for h in before]

    store_dir = tmp_path / "leases"
    _acquire(GROK_HOST, NOW, store_dir)
    _acquire(GROK_HOST, NOW + timedelta(seconds=1), store_dir)
    _acquire(RD_HOST, AFTER_EXPIRY, store_dir)
    prl.revoke_lease(role_id=ROLE_ID, host=RD_HOST, store_dir=store_dir, now=AFTER_EXPIRY)

    assert [h.to_dict() for h in holds_list.load()] == before_snapshot


def test_lease_denied_reason_vocabulary_is_closed() -> None:
    with pytest.raises(ValueError):
        prl.LeaseDenied(role_id=ROLE_ID, reason="not-a-real-reason")
