"""Web Push subscription persistence (SQLite).

Split from ``push.py`` to keep that module under the repo's 500-line soft cap
(same pattern ``routers.staff_knowledge`` / ``staff.models_insights`` use).
Owns the subscription domain types (``PushKeys``, ``PushSubscription``,
``PUSH_TOPICS``, ``DEFAULT_DB_PATH``) and the SQLite subscription store;
``push.py`` keeps the transport, crypto and API routes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from time_utils import utc_now_iso

PUSH_TOPICS = frozenset(
    {
        "agent.completed",
        "agent.failed",
        "ci.failed",
        "runner.offline",
        "queue.stale",
        "staff.escalation",
    }
)

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_PUSH_DB",
        str(Path(__file__).resolve().parents[1] / "config" / "push_subscriptions.sqlite3"),
    )
)


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=512)
    auth: str = Field(..., min_length=1, max_length=256)


@dataclass(frozen=True)
class PushSubscription:
    id: int
    user_id: str
    endpoint: str
    keys: dict[str, str]
    user_agent: str
    topics: tuple[str, ...]


def _db_path() -> Path:
    return Path(os.environ.get("RUNNER_DASHBOARD_PUSH_DB", str(DEFAULT_DB_PATH)))


MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            topics_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    current_version = conn.execute("SELECT IFNULL(MAX(version), 0) FROM schema_migrations").fetchone()[0]

    for version, ddl in MIGRATIONS:
        if version > current_version:
            with conn:
                conn.execute(ddl)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, utc_now_iso()),
                )


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    _run_migrations(conn)
    return conn


def _row_to_subscription(row: sqlite3.Row) -> PushSubscription:
    topics_payload = json.loads(row["topics_json"])
    topics = tuple(topic for topic in topics_payload if topic in PUSH_TOPICS)
    return PushSubscription(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        endpoint=str(row["endpoint"]),
        keys={"p256dh": str(row["p256dh"]), "auth": str(row["auth"])},
        user_agent=str(row["user_agent"]),
        topics=topics,
    )


def upsert_subscription(
    *,
    user_id: str,
    endpoint: str,
    keys: PushKeys,
    user_agent: str,
    topics: list[str],
    db_path: Path | None = None,
) -> PushSubscription:
    now = utc_now_iso()
    topics_json = json.dumps(topics, separators=(",", ":"))
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO push_subscriptions
                (user_id, endpoint, p256dh, auth, user_agent, topics_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth,
                user_agent = excluded.user_agent,
                topics_json = excluded.topics_json,
                updated_at = excluded.updated_at
            """,
            (user_id, endpoint, keys.p256dh, keys.auth, user_agent[:512], topics_json, now, now),
        )
        row = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
    if row is None:
        raise RuntimeError("subscription upsert did not return a row")
    return _row_to_subscription(row)


def delete_subscription(
    subscription_id: int,
    user_id: str,
    *,
    admin: bool = False,
    db_path: Path | None = None,
) -> bool:
    with _connect(db_path) as conn:
        if admin:
            cursor = conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))
        else:
            cursor = conn.execute(
                "DELETE FROM push_subscriptions WHERE id = ? AND user_id = ?",
                (subscription_id, user_id),
            )
        return cursor.rowcount > 0


def _subscriptions_for_topic(
    topic: str,
    user_id: str | None = None,
    db_path: Path | None = None,
) -> list[PushSubscription]:
    params: list[Any] = []
    query = "SELECT * FROM push_subscriptions"
    if user_id:
        query += " WHERE user_id = ?"
        params.append(user_id)
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    subscriptions = [_row_to_subscription(row) for row in rows]
    return [subscription for subscription in subscriptions if topic in subscription.topics]


def _delete_stale_subscription(subscription_id: int, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))
