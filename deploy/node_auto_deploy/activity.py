"""Fail-closed local staff-run and chat activity probe for release quiescence."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_STAFF_RUNS_RESPONSE_LIMIT = 1024 * 1024
_STAFF_ACTIVITY_TIMEOUT_SECONDS = 3.0


class ActivityUnavailableError(RuntimeError):
    """Activity state could not be established safely."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def fetch_staff_activity(base_url: str, token: str | None, database: Path) -> int:
    """Return the conservative active count from the local API and its full DB.

    The API request reads a small server-side aggregate
    (``/api/staff/activity/summary``), never a listing of runs. The local
    database count covers activity outside that aggregate. Neither failed
    source is interpreted as idle.
    """
    if not token or not token.strip():
        raise ActivityUnavailableError("staff activity summary probe requires a staff.read token")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ActivityUnavailableError("staff activity summary probe has an invalid loopback origin") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ActivityUnavailableError("staff activity summary probe must use an explicit loopback HTTP origin")

    url = f"{base_url.rstrip('/')}/api/staff/activity/summary"
    request = Request(url, headers={"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"})
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    try:
        with opener.open(request, timeout=_STAFF_ACTIVITY_TIMEOUT_SECONDS) as response:
            if response.getcode() != 200:
                raise ActivityUnavailableError("staff activity summary probe returned a non-200 status")
            body = response.read(_STAFF_RUNS_RESPONSE_LIMIT + 1)
        if len(body) > _STAFF_RUNS_RESPONSE_LIMIT:
            raise ActivityUnavailableError("staff activity summary probe response exceeded its size limit")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ActivityUnavailableError("staff activity summary probe returned an invalid response")
        active = payload.get("active")
        if not isinstance(active, int) or isinstance(active, bool) or active < 0:
            raise ActivityUnavailableError("staff activity summary probe returned an invalid active count")
    except ActivityUnavailableError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ActivityUnavailableError(f"staff activity summary probe unavailable: {type(exc).__name__}") from exc

    return max(active, local_activity_count(database))


def verify_staff_activity_probe(base_url: str, token: str | None, database: Path) -> int:
    """Post-restart probe: loopback activity summary + local DB (stdlib-only, no backend imports)."""
    return fetch_staff_activity(base_url, token, database)


def staff_database_path(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    configured = env.get("STAFF_RUNS_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    config_dir = env.get("RUNNER_DASHBOARD_CONFIG_DIR", "").strip()
    root = Path(config_dir).expanduser() if config_dir else Path.home() / ".config" / "runner-dashboard"
    return root / "staff_runs.sqlite3"


def local_activity_count(database: Path) -> int:
    """Count active staff runs and non-user pending/streaming chat replies.

    The database must already exist and contain both known tables/columns. An
    unknown or unreadable schema is not interpreted as an idle dashboard.
    """
    try:
        path = database.expanduser().resolve(strict=True)
        if not path.is_file():
            raise ActivityUnavailableError("staff activity database is not a regular file")
        uri = f"{path.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"runs", "messages"}.issubset(tables):
                raise ActivityUnavailableError("staff activity database schema is unavailable")
            run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
            if not {"status"}.issubset(run_columns) or not {"delivery", "author_kind"}.issubset(message_columns):
                raise ActivityUnavailableError("staff activity database schema is incomplete")
            active_runs = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'preparing', 'running')"
            ).fetchone()[0]
            pending_replies = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE author_kind != 'user' AND delivery IN ('pending', 'streaming')"
            ).fetchone()[0]
            return int(active_runs) + int(pending_replies)
    except ActivityUnavailableError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ActivityUnavailableError(f"could not read staff activity state: {exc}") from exc
