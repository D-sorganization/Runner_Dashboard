"""Bounded, complete, commit-specific GitHub check-run inventories."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from node_auto_deploy.check_runs import validate_check_run_record

_PAGE_SIZE = 100
_MAX_CHECKS = 5000


class CheckInventoryUnavailable(RuntimeError):
    """Incomplete CI evidence must never authorize deployment."""


def fetch_commit_checks(get: Callable[[str], Any], repo: str, sha: str) -> list[dict[str, Any]]:
    """Fetch every page, rejecting drift, duplicates and foreign commit evidence."""
    checks: list[dict[str, Any]] = []
    seen: set[int] = set()
    expected_total: int | None = None
    for page in range(1, _MAX_CHECKS // _PAGE_SIZE + 1):
        payload = get(f"/repos/{repo}/commits/{sha}/check-runs?per_page={_PAGE_SIZE}&page={page}")
        if not isinstance(payload, dict):
            raise CheckInventoryUnavailable("check inventory is not an object")
        total = payload.get("total_count")
        if isinstance(total, bool) or not isinstance(total, int) or not 0 <= total <= _MAX_CHECKS:
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
            if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 0 or identifier in seen:
                raise CheckInventoryUnavailable("check inventory has a missing or duplicate id")
            if record.get("head_sha") != sha:
                raise CheckInventoryUnavailable("check inventory belongs to another commit")
            seen.add(identifier)
            checks.append(record)
        if len(checks) == total:
            return checks
    raise CheckInventoryUnavailable("check inventory exceeds pagination budget")
