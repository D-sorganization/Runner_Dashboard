"""Atomic idempotency reservation for Staff API v1 (BR-01, Issue #1795).

The ledger reserves (key, endpoint, principal) before any effect runs, so two
connections with the same key cannot both execute; it fingerprints the payload
so a reused key with a different body is refused; and a reservation whose
outcome was never recorded is reported as unknown instead of re-executed.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.idempotency import IdempotencyStore, payload_fingerprint  # noqa: E402

KEY, ENDPOINT, WHO = "k-1", "POST /api/v1/staff/ad-hoc/run", "human:operator"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "runs.sqlite3"


@pytest.mark.unit
def test_fingerprint_ignores_key_order_and_whitespace() -> None:
    assert payload_fingerprint({"a": 1, "b": [1, 2]}) == payload_fingerprint({"b": [1, 2], "a": 1})
    assert payload_fingerprint({"a": 1}) != payload_fingerprint({"a": 2})


@pytest.mark.unit
def test_concurrent_reservations_on_two_connections_admit_exactly_one(db: Path) -> None:
    stores = [IdempotencyStore(db), IdempotencyStore(db)]
    barrier = threading.Barrier(len(stores))
    states: list[str] = []

    def reserve(store: IdempotencyStore) -> None:
        barrier.wait()
        states.append(store.reserve(KEY, ENDPOINT, WHO, "h1").state)

    threads = [threading.Thread(target=reserve, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(states) == ["acquired", "in_progress"]


@pytest.mark.unit
def test_changed_payload_under_the_same_key_is_a_mismatch(db: Path) -> None:
    store = IdempotencyStore(db)
    assert store.reserve(KEY, ENDPOINT, WHO, "h1").state == "acquired"
    assert store.reserve(KEY, ENDPOINT, WHO, "h2").state == "mismatch"


@pytest.mark.unit
def test_completed_reservation_replays_the_original_receipt(db: Path) -> None:
    store = IdempotencyStore(db)
    first = store.reserve(KEY, ENDPOINT, WHO, "h1")
    store.complete(KEY, ENDPOINT, WHO, 201, {"Content-Type": "application/json"}, '{"id": "run-1"}')

    again = store.reserve(KEY, ENDPOINT, WHO, "h1")
    assert again.state == "replay"
    assert again.record is not None
    assert (again.record.status_code, again.record.response_body) == (201, '{"id": "run-1"}')
    assert again.operation_id == first.operation_id


@pytest.mark.unit
def test_released_reservation_can_be_taken_again(db: Path) -> None:
    store = IdempotencyStore(db)
    store.reserve(KEY, ENDPOINT, WHO, "h1")
    store.release(KEY, ENDPOINT, WHO)
    assert store.reserve(KEY, ENDPOINT, WHO, "h1").state == "acquired"


@pytest.mark.unit
def test_lapsed_reservation_is_unknown_unless_the_operation_is_safe_to_take_over(db: Path) -> None:
    store = IdempotencyStore(db)
    first = store.reserve(KEY, ENDPOINT, WHO, "h1", lease_seconds=-1)

    unknown = store.reserve(KEY, ENDPOINT, WHO, "h1")
    assert unknown.state == "unknown_outcome"
    assert unknown.operation_id == first.operation_id

    taken = store.reserve(KEY, ENDPOINT, WHO, "h1", takeover_safe=True)
    assert taken.state == "acquired"
    assert taken.recovered is True
    assert taken.operation_id == first.operation_id


@pytest.mark.unit
def test_expired_receipt_frees_the_key(db: Path) -> None:
    store = IdempotencyStore(db)
    store.reserve(KEY, ENDPOINT, WHO, "h1")
    store.complete(KEY, ENDPOINT, WHO, 200, {}, "{}", ttl_seconds=-1)
    assert store.reserve(KEY, ENDPOINT, WHO, "h2").state == "acquired"


@pytest.mark.unit
def test_pending_rows_are_invisible_to_get(db: Path) -> None:
    store = IdempotencyStore(db)
    store.reserve(KEY, ENDPOINT, WHO, "h1")
    assert store.get(KEY, ENDPOINT, WHO) is None


@pytest.mark.unit
def test_legacy_ledger_is_migrated_and_its_rows_replay(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE idempotency_keys (
            key TEXT NOT NULL, endpoint TEXT NOT NULL, principal TEXT NOT NULL,
            status_code INTEGER NOT NULL, response_headers TEXT NOT NULL,
            response_body TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY (key, endpoint, principal)
        );
        INSERT INTO idempotency_keys VALUES
            ('k-1', 'POST /api/v1/staff/ad-hoc/run', 'human:operator', 200, '{}', '{"old": true}',
             '2026-09-29T00:00:00Z', '2999-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    replay = IdempotencyStore(db).reserve(KEY, ENDPOINT, WHO, "any-hash")
    assert replay.state == "replay"
    assert replay.record is not None
    assert replay.record.response_body == '{"old": true}'
