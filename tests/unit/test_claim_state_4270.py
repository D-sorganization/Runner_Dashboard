"""ClaimState enum, transitions, and durable compare-and-set acquire (#4270 PR 2)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from coordination import claim_state


@pytest.mark.unit
def test_claim_state_transition_table_is_closed() -> None:
    for state in claim_state.ClaimState:
        assert state in claim_state.LEGAL_TRANSITIONS
        for target in claim_state.LEGAL_TRANSITIONS[state]:
            same_holder = state == claim_state.ClaimState.HELD and target == claim_state.ClaimState.HELD
            assert claim_state.is_legal_transition(state, target, same_holder=same_holder)


@pytest.mark.unit
def test_unheld_to_held_is_legal_other_holder_is_not() -> None:
    assert claim_state.is_legal_transition(claim_state.ClaimState.UNHELD, claim_state.ClaimState.HELD)
    assert not claim_state.is_legal_transition(
        claim_state.ClaimState.HELD, claim_state.ClaimState.HELD, same_holder=False
    )
    assert claim_state.is_legal_transition(claim_state.ClaimState.HELD, claim_state.ClaimState.HELD, same_holder=True)
    assert claim_state.is_legal_transition(claim_state.ClaimState.HELD, claim_state.ClaimState.UNHELD)


@pytest.mark.unit
def test_acquire_compare_and_set_second_concurrent_loses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "claim_state.sqlite3"
    monkeypatch.setenv("CLAIM_STATE_DB", str(db))
    claim_state.reset_claim_state_store()
    store = claim_state.get_store()
    repo, issue = "Tools", 4270
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def racer(agent: str, session: str) -> None:
        barrier.wait(5)
        res = store.try_acquire(repo, issue, agent="codex", session=session)
        outcomes.append("ok" if res.acquired else "held")

    threads = [
        threading.Thread(target=racer, args=("codex", "codex-a")),
        threading.Thread(target=racer, args=("codex", "codex-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcomes.count("ok") == 1
    assert outcomes.count("held") == 1


@pytest.mark.unit
def test_acquire_renewal_same_agent_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIM_STATE_DB", str(tmp_path / "claim_state.sqlite3"))
    claim_state.reset_claim_state_store()
    store = claim_state.get_store()
    first = store.try_acquire("Tools", 1, agent="codex", session="codex-1")
    second = store.try_acquire("Tools", 1, agent="codex", session="codex-1")
    assert first.acquired and second.acquired
    row = store.read("Tools", 1)
    assert row is not None and row.state == claim_state.ClaimState.HELD


@pytest.mark.unit
def test_release_clears_durable_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIM_STATE_DB", str(tmp_path / "claim_state.sqlite3"))
    claim_state.reset_claim_state_store()
    store = claim_state.get_store()
    assert store.try_acquire("Tools", 9, agent="codex", session="codex-9").acquired
    store.release("Tools", 9, agent="codex", session="codex-9")
    assert store.read("Tools", 9) is None


@pytest.mark.unit
def test_acquire_blocks_different_holder_until_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIM_STATE_DB", str(tmp_path / "claim_state.sqlite3"))
    monkeypatch.setenv("CLAIM_STATE_CAS_TTL_S", "60")
    claim_state.reset_claim_state_store()
    store = claim_state.get_store()
    assert store.try_acquire("Tools", 42, agent="codex", session="codex-a", reserve_ttl_s=0.05).acquired
    blocked = store.try_acquire("Tools", 42, agent="cursor", session="cursor-b")
    assert not blocked.acquired
    assert blocked.holder_agent == "codex"
    time.sleep(0.06)
    taken = store.try_acquire("Tools", 42, agent="cursor", session="cursor-b", reserve_ttl_s=60)
    assert taken.acquired
    row = store.read("Tools", 42)
    assert row is not None and row.agent == "cursor" and row.session == "cursor-b"


@pytest.mark.unit
def test_active_holder_still_blocks_before_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIM_STATE_DB", str(tmp_path / "claim_state.sqlite3"))
    claim_state.reset_claim_state_store()
    store = claim_state.get_store()
    assert store.try_acquire("Tools", 43, agent="codex", session="codex-a", reserve_ttl_s=3600).acquired
    blocked = store.try_acquire("Tools", 43, agent="cursor", session="cursor-b")
    assert not blocked.acquired and blocked.holder_expires_at is not None
