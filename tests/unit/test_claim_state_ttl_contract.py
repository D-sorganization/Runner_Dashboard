"""TTL contract for ClaimStateStore.try_acquire: invalid TTLs must not mint a claim that is instantly stealable."""

from __future__ import annotations

import pytest
from coordination.claim_state import ClaimStateStore


@pytest.fixture
def store(tmp_path):
    s = ClaimStateStore(tmp_path / "claims.sqlite3")
    yield s
    s.close()


@pytest.mark.parametrize("bad_ttl", [0, -5.0, float("nan"), float("inf"), float("-inf")])
def test_try_acquire_rejects_non_positive_or_non_finite_ttl(store, bad_ttl):
    with pytest.raises(ValueError, match="reserve_ttl_s"):
        store.try_acquire("o/r", 1, agent="a", session="s1", reserve_ttl_s=bad_ttl)
    # Nothing was written: a later valid acquire by someone else succeeds cleanly.
    assert store.read("o/r", 1) is None
    assert store.try_acquire("o/r", 1, agent="b", session="s2", reserve_ttl_s=60).acquired


def test_zero_ttl_claim_is_not_stealable_regression(store):
    """Before the fix a 0s TTL 'acquired' but a rival immediately took the claim."""
    try:
        first = store.try_acquire("o/r", 2, agent="a", session="s1", reserve_ttl_s=0)
    except ValueError:
        return
    rival = store.try_acquire("o/r", 2, agent="b", session="s2", reserve_ttl_s=60)
    assert not (first.acquired and rival.acquired), "two holders both won the same claim"


def test_env_ttl_invalid_falls_back_to_default(monkeypatch):
    from coordination import claim_state

    monkeypatch.setenv("CLAIM_STATE_CAS_TTL_S", "garbage")
    assert claim_state.cas_reserve_ttl_seconds() == 300.0
    monkeypatch.setenv("CLAIM_STATE_CAS_TTL_S", "-1")
    assert claim_state.cas_reserve_ttl_seconds() == 300.0
