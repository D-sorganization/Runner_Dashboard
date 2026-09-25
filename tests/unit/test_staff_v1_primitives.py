"""Unit tests for Staff API v1 primitives (SC-F3, Issue #1312).

Tests:
- IdempotencyStore: save, get, scoping by (endpoint, principal), TTL pruning (24h).
- Cursor pagination: encode, decode, keyset pagination, malformed cursor handling.
- Error envelope: formatting from various exception detail shapes.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from staff.idempotency import IdempotencyStore
from staff.pagination import (
    decode_cursor,
    encode_cursor,
    paginate_items,
)
from staff.v1_envelope import format_error_envelope


def test_idempotency_store_save_and_retrieve(tmp_path: Any) -> None:
    db_path = str(tmp_path / "idempotency.sqlite3")
    store = IdempotencyStore(path=db_path)

    key = "idem-test-key-1"
    endpoint = "POST /api/v1/staff/night-watch/run"
    principal = "operator-user"
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"status": "ok", "run_id": "run-123"})

    # Key does not exist initially
    assert store.get(key, endpoint, principal) is None

    # Save record
    record = store.save(
        key=key,
        endpoint=endpoint,
        principal=principal,
        status_code=200,
        response_headers=headers,
        response_body=body,
    )
    assert record.key == key
    assert record.status_code == 200
    assert record.response_body == body

    # Retrieve record
    retrieved = store.get(key, endpoint, principal)
    assert retrieved is not None
    assert retrieved.key == key
    assert retrieved.response_body == body
    assert retrieved.status_code == 200

    # Scoping by endpoint and principal
    assert store.get(key, "POST /different/endpoint", principal) is None
    assert store.get(key, endpoint, "other-principal") is None
    assert store.get("other-key", endpoint, principal) is None


def test_idempotency_store_ttl_and_pruning(tmp_path: Any) -> None:
    db_path = str(tmp_path / "idempotency.sqlite3")
    store = IdempotencyStore(path=db_path)

    key_fresh = "fresh-key"
    key_expired = "expired-key"
    endpoint = "POST /api/v1/staff/test"
    principal = "user-1"

    store.save(
        key=key_fresh,
        endpoint=endpoint,
        principal=principal,
        status_code=200,
        response_headers={},
        response_body="{}",
        ttl_seconds=86400,
    )

    # Manually backdate expired key
    with store._lock:
        expired_ts = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        store._conn.execute(
            """
            INSERT INTO idempotency_keys
            (key, endpoint, principal, status_code, response_headers, response_body, created_at, expires_at)
            VALUES (?, ?, ?, 200, '{}', '{}', ?, ?)
            """,
            (key_expired, endpoint, principal, expired_ts, expired_ts),
        )

    # Getting expired key returns None
    assert store.get(key_expired, endpoint, principal) is None
    # Getting fresh key returns record
    assert store.get(key_fresh, endpoint, principal) is not None

    # Prune expired keys
    pruned = store.prune_expired()
    assert pruned >= 1

    # Verify expired key was removed
    with store._lock:
        cur = store._conn.execute("SELECT count(*) FROM idempotency_keys WHERE key = ?", (key_expired,))
        assert cur.fetchone()[0] == 0


def test_pagination_encode_decode() -> None:
    ts = "2026-09-24T12:00:00Z"
    item_id = "item-abc-123"
    cursor = encode_cursor(ts, item_id)
    assert isinstance(cursor, str)

    dec_ts, dec_id = decode_cursor(cursor)
    assert dec_ts == ts
    assert dec_id == item_id


def test_pagination_decode_malformed_cursor() -> None:
    # Non-base64 string
    with pytest.raises(HTTPException) as exc_info:
        decode_cursor("not-valid-base64@@@")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_cursor"

    # Base64 valid but json invalid
    bad_json = base64.urlsafe_b64encode(b"not json").decode("ascii")
    with pytest.raises(HTTPException) as exc_info2:
        decode_cursor(bad_json)
    assert exc_info2.value.status_code == 400
    assert exc_info2.value.detail["code"] == "invalid_cursor"

    # Base64 valid json but missing keys
    missing_keys = base64.urlsafe_b64encode(json.dumps({"foo": "bar"}).encode("utf-8")).decode("ascii")
    with pytest.raises(HTTPException) as exc_info3:
        decode_cursor(missing_keys)
    assert exc_info3.value.status_code == 400
    assert exc_info3.value.detail["code"] == "invalid_cursor"


def test_pagination_keyset_slicing() -> None:
    items = [
        {"created_at": f"2026-09-24T10:0{i}:00Z", "id": f"id-{i}"} for i in reversed(range(10))
    ]  # 9 down to 0 (sorted newest first)

    # First page: limit 3
    page1 = paginate_items(items, limit=3)
    assert len(page1.items) == 3
    assert page1.items[0]["id"] == "id-9"
    assert page1.has_more is True
    assert page1.next_cursor is not None

    # Second page: limit 3
    page2 = paginate_items(items, limit=3, cursor=page1.next_cursor)
    assert len(page2.items) == 3
    assert page2.items[0]["id"] == "id-6"
    assert page2.has_more is True

    # Empty list
    empty_page = paginate_items([], limit=5)
    assert len(empty_page.items) == 0
    assert empty_page.has_more is False
    assert empty_page.next_cursor is None


def test_format_error_envelope() -> None:
    # 1. Simple 404
    env_404 = format_error_envelope(404, "Item not found", request_id="req-123")
    assert env_404["error"]["code"] == "not_found"
    assert env_404["error"]["message"] == "Item not found"
    assert env_404["error"]["retryable"] is False
    assert env_404["error"]["request_id"] == "req-123"

    # 2. Pydantic validation errors
    val_errors = [
        {"loc": ["body", "repo"], "msg": "field required", "type": "value_error.missing"},
    ]
    env_val = format_error_envelope(422, val_errors, request_id="req-456")
    assert env_val["error"]["code"] == "validation_error"
    assert "body.repo: field required" in env_val["error"]["message"]
    assert env_val["error"]["retryable"] is False

    # 3. Custom dict detail
    custom = {
        "code": "quota_exceeded",
        "message": "Daily budget limit reached",
        "retryable": False,
        "hint": "Request a budget increase.",
    }
    env_custom = format_error_envelope(403, custom, request_id="req-789")
    assert env_custom["error"]["code"] == "quota_exceeded"
    assert env_custom["error"]["message"] == "Daily budget limit reached"
    assert env_custom["error"]["hint"] == "Request a budget increase."
