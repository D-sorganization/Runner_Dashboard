"""Tests for push_store.py — subscription types and SQLite persistence.

Covers the store surface split out of ``push.py``: ``PushKeys`` validation,
insert/upsert round trips through the real SQLite file, idempotent store
initialisation (migrations) on fresh and existing database paths, the
ownership rules of ``delete_subscription``, and the failure path when an
upsert fails to persist a row.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pydantic
import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

import push_store  # noqa: E402
from push_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    MIGRATIONS,
    PUSH_TOPICS,
    PushKeys,
    PushSubscription,
    _connect,
    _row_to_subscription,
    _subscriptions_for_topic,
    delete_subscription,
    upsert_subscription,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Provide a throwaway SQLite path for each test."""
    return tmp_path / "push_store_test.sqlite3"


def _keys(p256dh: str = "AAAA" * 30, auth: str = "BBBB" * 10) -> PushKeys:
    return PushKeys(p256dh=p256dh, auth=auth)


def _upsert(
    db: Path,
    endpoint: str = "https://push.example.com/sub/1",
    user_id: str = "user-1",
    user_agent: str = "pytest-agent/1.0",
    topics: list[str] | None = None,
) -> PushSubscription:
    return upsert_subscription(
        user_id=user_id,
        endpoint=endpoint,
        keys=_keys(),
        user_agent=user_agent,
        topics=topics if topics is not None else ["agent.completed"],
        db_path=db,
    )


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


def test_push_keys_accepts_valid_keys() -> None:
    keys = _keys()
    assert keys.p256dh == "AAAA" * 30
    assert keys.auth == "BBBB" * 10


def test_push_keys_rejects_empty_p256dh() -> None:
    with pytest.raises(pydantic.ValidationError):
        PushKeys(p256dh="", auth="bbbb")


def test_push_keys_rejects_empty_auth() -> None:
    with pytest.raises(pydantic.ValidationError):
        PushKeys(p256dh="aaaa", auth="")


def test_push_topics_covers_expected_notification_kinds() -> None:
    assert PUSH_TOPICS == frozenset(
        {
            "agent.completed",
            "agent.failed",
            "ci.failed",
            "runner.offline",
            "queue.stale",
            "staff.escalation",
        }
    )


def test_default_db_path_lives_in_config() -> None:
    assert DEFAULT_DB_PATH.name == "push_subscriptions.sqlite3"
    assert DEFAULT_DB_PATH.parent.name == "config"


# ---------------------------------------------------------------------------
# Store initialisation / migrations
# ---------------------------------------------------------------------------


def test_store_init_fresh_db_creates_schema(db_path: Path) -> None:
    _upsert(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "push_subscriptions" in tables
    assert "schema_migrations" in tables


def test_store_init_idempotent_on_existing_db(db_path: Path) -> None:
    first = _upsert(db_path)
    # Reopen the same path several times — migrations must not re-apply or corrupt rows.
    again = upsert_subscription(
        user_id="user-1",
        endpoint=first.endpoint,
        keys=_keys(auth="CCCC" * 10),
        user_agent="pytest-agent/2.0",
        topics=["ci.failed"],
        db_path=db_path,
    )
    assert again.id == first.id  # same row updated, not duplicated
    applied = MIGRATIONS  # only version 1 exists
    assert [version for version, _ in applied] == [1]
    conn = sqlite3.connect(db_path)
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    count = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    conn.close()
    assert versions == [1]  # exactly one migration applied once
    assert count == 1


def test_store_init_idempotent_across_store_module_use(db_path: Path) -> None:
    _connect(db_path).close()
    _connect(db_path).close()
    row = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM push_subscriptions"
    ).fetchone()[0]
    assert row == 0  # opening a store never seeds data


# ---------------------------------------------------------------------------
# upsert_subscription — insert / update round trip
# ---------------------------------------------------------------------------


def test_upsert_subscription_insert_persists_all_fields(db_path: Path) -> None:
    keys = PushKeys(p256dh="K" * 87, auth="A" * 22)
    sub = upsert_subscription(
        user_id="user-42",
        endpoint="https://push.example.com/full",
        keys=keys,
        user_agent="ua-string/9.1",
        topics=["agent.failed", "staff.escalation"],
        db_path=db_path,
    )
    assert sub.user_id == "user-42"
    assert sub.endpoint == "https://push.example.com/full"
    assert sub.keys == {"p256dh": "K" * 87, "auth": "A" * 22}
    assert sub.user_agent == "ua-string/9.1"
    assert sub.topics == ("agent.failed", "staff.escalation")


def test_upsert_subscription_round_trip_via_topic_lookup(db_path: Path) -> None:
    created = _upsert(db_path, topics=["runner.offline", "queue.stale"])
    matches = _subscriptions_for_topic("queue.stale", db_path=db_path)
    assert len(matches) == 1
    found = matches[0]
    assert found == created  # frozen dataclass equality proves full persistence
    assert found.user_agent == "pytest-agent/1.0"
    assert not _subscriptions_for_topic("ci.failed", db_path=db_path)


def test_upsert_subscription_topic_filter_scopes_to_user(db_path: Path) -> None:
    _upsert(db_path, endpoint="https://push.example.com/u1", user_id="user-1", topics=["ci.failed"])
    _upsert(db_path, endpoint="https://push.example.com/u2", user_id="user-2", topics=["ci.failed"])
    mine = _subscriptions_for_topic("ci.failed", user_id="user-2", db_path=db_path)
    assert [s.user_id for s in mine] == ["user-2"]


def test_upsert_subscription_rejects_unknown_topic_via_row_filter(db_path: Path) -> None:
    # Unknown topics are dropped at read time by _row_to_subscription.
    _upsert(db_path, topics=["agent.completed", "made.up.topic"])
    matches = _subscriptions_for_topic("agent.completed", db_path=db_path)
    assert len(matches) == 1
    assert matches[0].topics == ("agent.completed",)
    assert _subscriptions_for_topic("made.up.topic", db_path=db_path) == []


def test_upsert_subscription_truncates_oversized_user_agent(db_path: Path) -> None:
    sub = _upsert(db_path, user_agent="u" * 900)
    assert len(sub.user_agent) <= 512


# ---------------------------------------------------------------------------
# upsert failure path
# ---------------------------------------------------------------------------


def test_upsert_invalid_keys_propagates_validation_error(db_path: Path) -> None:
    with pytest.raises(pydantic.ValidationError):
        upsert_subscription(
            user_id="user-1",
            endpoint="https://push.example.com/bad",
            keys=PushKeys(p256dh="", auth="auth"),
            user_agent="pytest",
            topics=["agent.completed"],
            db_path=db_path,
        )


def test_upsert_missing_row_raises_runtime_error(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression guard for the designed failure path: if the INSERT fails to
    # produce a readable row, upsert_subscription must raise RuntimeError.
    real_connect = push_store._connect

    class _NoRowCursor:
        def __init__(self, cursor: sqlite3.Cursor) -> None:
            self._cursor = cursor

        def fetchone(self) -> None:
            return None

        def __getattr__(self, name: str) -> object:
            return getattr(self._cursor, name)

    class _NoRowConn:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __enter__(self) -> _NoRowConn:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            self._conn.__exit__(exc_type, exc, tb)  # type: ignore[arg-type]

        def execute(self, sql: str, params: object = ()) -> _NoRowCursor | sqlite3.Cursor:
            cursor = self._conn.execute(sql, params)  # type: ignore[arg-type]
            if sql.strip().startswith("SELECT * FROM push_subscriptions WHERE endpoint"):
                return _NoRowCursor(cursor)
            return cursor

    def proxy_connect(path: object | None = None) -> _NoRowConn:
        conn = real_connect(path)  # type: ignore[arg-type]
        return _NoRowConn(conn)

    monkeypatch.setattr(push_store, "_connect", proxy_connect)
    with pytest.raises(RuntimeError, match="did not return a row"):
        upsert_subscription(
            user_id="user-1",
            endpoint="https://push.example.com/missing",
            keys=_keys(),
            user_agent="pytest",
            topics=["agent.completed"],
            db_path=db_path,
        )
    real_connect(db_path).close()


# ---------------------------------------------------------------------------
# _row_to_subscription hardening
# ---------------------------------------------------------------------------


def test_row_to_subscription_filters_unknown_topics() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY, user_id TEXT, endpoint TEXT, p256dh TEXT,
            auth TEXT, user_agent TEXT, topics_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO push_subscriptions VALUES (1, 'u', 'ep', 'p', 'a', 'ua', ?)",
        ('["agent.completed","bogus.topic"]',),
    )
    row = conn.execute("SELECT * FROM push_subscriptions").fetchone()
    conn.close()
    sub = _row_to_subscription(row)
    assert isinstance(sub, PushSubscription)
    assert sub.topics == ("agent.completed",)


# ---------------------------------------------------------------------------
# delete_subscription ownership rules
# ---------------------------------------------------------------------------


def test_delete_subscription_round_trip(db_path: Path) -> None:
    sub = _upsert(db_path)
    assert delete_subscription(sub.id, "user-1", db_path=db_path) is True
    assert _subscriptions_for_topic("agent.completed", db_path=db_path) == []
    # Deleting the same row again is a no-op.
    assert delete_subscription(sub.id, "user-1", db_path=db_path) is False
