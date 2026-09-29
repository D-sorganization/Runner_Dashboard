"""Idempotent admission for Staff API v1 mutating routes (BR-01, Issue #1795).

The review reproduced two concurrent same-key calls both running the action
(observed count: 2). The key is now reserved before the action, a changed body
under a reused key is refused, and a result that could not be recorded is
reported without inviting a blind re-run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from routers import staff_v1
from staff import idempotency
from staff.idempotency import IdempotencyStore, IdempotencyStoreError

ENDPOINT = "POST /api/v1/staff/ad-hoc/run"
WHO = "human:operator"


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IdempotencyStore:
    store = IdempotencyStore(tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(staff_v1, "get_idempotency_store", lambda: store)
    return store


def test_concurrent_same_key_requests_run_the_action_once(ledger: IdempotencyStore) -> None:
    calls = 0

    async def scenario() -> list[Any]:
        release = asyncio.Event()

        async def action() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            await release.wait()
            return {"run_id": "run-1"}

        first = asyncio.create_task(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, action))
        await asyncio.sleep(0)
        second = asyncio.create_task(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, action))
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(first, second, return_exceptions=True)

    first, second = asyncio.run(scenario())

    assert calls == 1
    assert first.status_code == 200
    assert isinstance(second, HTTPException)
    assert second.status_code == 409
    assert second.detail["code"] == "idempotency_in_progress"
    assert second.detail["retryable"] is True


def test_sequential_replay_returns_the_original_status_and_body(ledger: IdempotencyStore) -> None:
    calls = 0

    def action() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"run_id": f"run-{calls}"}

    first = asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, action))
    again = asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, action))

    assert calls == 1
    assert again.body == first.body
    assert again.status_code == first.status_code
    assert again.headers["Idempotent-Replay"] == "true"


def test_reused_key_with_a_different_body_is_refused(ledger: IdempotencyStore) -> None:
    asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, lambda: {"ok": 1}))

    with pytest.raises(HTTPException) as err:
        asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 2}, lambda: {"ok": 2}))

    assert err.value.status_code == 409
    assert err.value.detail["code"] == "idempotency_key_reused"


def test_a_failed_action_frees_the_key_for_a_retry(ledger: IdempotencyStore) -> None:
    def boom() -> dict[str, Any]:
        raise HTTPException(status_code=404, detail="no such role")

    with pytest.raises(HTTPException):
        asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, boom))

    retry = asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, lambda: {"ok": True}))
    assert retry.status_code == 200


def test_an_unrecorded_result_is_returned_and_a_retry_does_not_rerun(
    ledger: IdempotencyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def action() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"run_id": "run-1"}

    def broken_complete(*_args: Any, **_kwargs: Any) -> None:
        raise IdempotencyStoreError("disk full")

    monkeypatch.setattr(ledger, "complete", broken_complete)
    first = asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, action))
    assert first.status_code == 200
    assert first.headers["Idempotency-Receipt"] == "unrecorded"

    with pytest.raises(HTTPException) as err:
        asyncio.run(staff_v1._handle_idempotent_post("k", ENDPOINT, WHO, {"p": 1}, action))
    assert err.value.status_code == 409
    assert calls == 1


def test_a_lapsed_dispatch_is_unknown_but_a_safe_action_is_retaken(
    ledger: IdempotencyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger.reserve("d", ENDPOINT, WHO, idempotency.payload_fingerprint({"p": 1}), lease_seconds=-1)
    with pytest.raises(HTTPException) as err:
        asyncio.run(staff_v1._handle_idempotent_post("d", ENDPOINT, WHO, {"p": 1}, lambda: {"ok": 1}))
    assert err.value.detail["code"] == "idempotency_outcome_unknown"

    cancel = "POST /api/v1/staff/runs/r1/cancel"
    ledger.reserve("c", cancel, WHO, idempotency.payload_fingerprint({}), lease_seconds=-1)
    done = asyncio.run(
        staff_v1._handle_idempotent_post("c", cancel, WHO, {}, lambda: {"cancelled": True}, takeover_safe=True)
    )
    assert done.status_code == 200
