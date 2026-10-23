"""Bounded, complete, commit-specific GitHub check-run inventories."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from node_auto_deploy.check_runs import (
    FAILING_CONCLUSIONS,
    PASSING_CONCLUSIONS,
    PENDING_STATUSES,
    normalize_commit_sha,
    validate_check_run_record,
)

_PAGE_SIZE = 100
MAX_CHECK_RUNS = 5000


class CheckInventoryUnavailable(RuntimeError):
    """Incomplete CI evidence must never authorize deployment."""


def _positive_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _started_at(value: Any) -> datetime | None:
    """Parse GitHub's documented nullable RFC3339 CheckRun start timestamp."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise CheckInventoryUnavailable("check inventory has an invalid started_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckInventoryUnavailable("check inventory has an invalid started_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CheckInventoryUnavailable("check inventory started_at must include a timezone")
    return parsed.astimezone(timezone.utc)  # noqa: UP017 - runtime supports Python 3.10


def normalize_latest_check_runs(checks: list[dict[str, Any]], expected_sha: str) -> list[dict[str, Any]]:
    """Keep each app/suite/name's newest CheckRun; do not invent run_attempt."""
    sha = normalize_commit_sha(expected_sha)
    if sha is None:
        raise CheckInventoryUnavailable("expected check inventory commit is invalid")
    groups: dict[tuple[int, int, str], list[tuple[int, datetime | None, dict[str, Any]]]] = {}
    for record in checks:
        if validate_check_run_record(record) is not None:
            raise CheckInventoryUnavailable("check inventory contains a malformed record")
        head_sha = record.get("head_sha")
        if not isinstance(head_sha, str) or normalize_commit_sha(head_sha) != sha:
            raise CheckInventoryUnavailable("check inventory belongs to another commit")
        status = record.get("status")
        conclusion = record.get("conclusion")
        if not isinstance(status, str) or status not in PENDING_STATUSES | {"completed"}:
            raise CheckInventoryUnavailable("check inventory has an invalid status")
        if status == "completed":
            valid_conclusions = PASSING_CONCLUSIONS | FAILING_CONCLUSIONS | {"stale"}
            if not isinstance(conclusion, str) or conclusion not in valid_conclusions:
                raise CheckInventoryUnavailable("check inventory has an invalid completed conclusion")
        elif conclusion is not None:
            raise CheckInventoryUnavailable("pending check inventory record has a conclusion")
        run_id = _positive_id(record.get("id"))
        app = record.get("app")
        app_id = _positive_id(app.get("id")) if isinstance(app, dict) else None
        suite = record.get("check_suite")
        suite_id = _positive_id(suite.get("id")) if isinstance(suite, dict) else None
        name = record.get("name")
        if "started_at" not in record:
            raise CheckInventoryUnavailable("check inventory is missing started_at")
        started = _started_at(record.get("started_at"))
        if run_id is None or app_id is None or suite_id is None or not isinstance(name, str) or not name:
            raise CheckInventoryUnavailable("check inventory has invalid attempt identity metadata")
        groups.setdefault((app_id, suite_id, name), []).append((run_id, started, record))

    latest: list[dict[str, Any]] = []
    for attempts in groups.values():
        attempts.sort(key=lambda item: item[0])
        previous_started: datetime | None = None
        for _, started, _ in attempts:
            if started is not None and previous_started is not None and started < previous_started:
                raise CheckInventoryUnavailable("check-run ids and started_at are not monotonic")
            if started is not None:
                previous_started = started
        latest.append(attempts[-1][2])
    return latest


def fetch_commit_checks(get: Callable[[str], Any], repo: str, sha: str) -> list[dict[str, Any]]:
    """Fetch all check-run attempts, then retain the newest per app/suite/name."""
    checks: list[dict[str, Any]] = []
    seen: set[int] = set()
    expected_total: int | None = None
    for page in range(1, MAX_CHECK_RUNS // _PAGE_SIZE + 1):
        payload = get(f"/repos/{repo}/commits/{sha}/check-runs?per_page={_PAGE_SIZE}&filter=all&page={page}")
        if not isinstance(payload, dict):
            raise CheckInventoryUnavailable("check inventory is not an object")
        total = payload.get("total_count")
        if isinstance(total, bool) or not isinstance(total, int) or not 0 <= total <= MAX_CHECK_RUNS:
            raise CheckInventoryUnavailable("check inventory count is missing or out of bounds")
        if expected_total is not None and total != expected_total:
            raise CheckInventoryUnavailable("check inventory changed during pagination")
        expected_total = total
        records = payload.get("check_runs")
        if not isinstance(records, list) or len(records) != min(_PAGE_SIZE, total - len(checks)):
            raise CheckInventoryUnavailable("check inventory page is incomplete")
        for record in records:
            if validate_check_run_record(record) is not None:
                raise CheckInventoryUnavailable("check inventory contains a malformed record")
            identifier = record.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0 or identifier in seen:
                raise CheckInventoryUnavailable("check inventory has a missing or duplicate id")
            if record.get("head_sha") != sha:
                raise CheckInventoryUnavailable("check inventory belongs to another commit")
            seen.add(identifier)
            checks.append(record)
        if len(checks) == total:
            return normalize_latest_check_runs(checks, sha)
    raise CheckInventoryUnavailable("check inventory exceeds pagination budget")
