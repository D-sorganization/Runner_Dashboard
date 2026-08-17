"""Shared GitHub Actions runner inventory helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

RUNNERS_PAGE_SIZE = 100
MAX_RUNNER_PAGES = 20

GitHubApi = Callable[[str], Awaitable[dict[str, Any]]]


class IncompleteInventoryError(RuntimeError):
    """Raised when GitHub pagination yields fewer runners than total_count."""


def _runner_page_endpoint(org: str, page: int) -> str:
    assert org.strip(), "org must not be empty"
    assert page >= 1, "page must be positive"
    return f"/orgs/{org}/actions/runners?per_page={RUNNERS_PAGE_SIZE}&page={page}"


async def fetch_org_runners(
    api: GitHubApi,
    org: str,
    *,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Fetch the full organization runner inventory across GitHub pages."""
    first_page = await api(_runner_page_endpoint(org, 1))
    total_count = _coerce_total_count(first_page)
    runners = _unique_runners(first_page.get("runners", []) or [])

    page = 2
    while len(runners) < total_count and page <= MAX_RUNNER_PAGES:
        next_page = await api(_runner_page_endpoint(org, page))
        batch = next_page.get("runners", []) or []
        if not batch:
            break
        runners = _unique_runners([*runners, *batch])
        page += 1

    if len(runners) < total_count and not allow_partial:
        raise IncompleteInventoryError(
            f"Incomplete runner inventory: fetched {len(runners)} of {total_count} runners for org '{org}'"
        )

    return {
        **first_page,
        "runners": runners,
        "total_count": max(total_count, len(runners)),
    }


def _coerce_total_count(payload: dict[str, Any]) -> int:
    raw_total = payload.get("total_count", len(payload.get("runners", []) or []))
    try:
        return max(0, int(raw_total))
    except (TypeError, ValueError):
        return len(payload.get("runners", []) or [])


def _unique_runners(runners: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_ids: set[int] = set()
    unique: list[dict[str, Any]] = []
    for runner in runners:
        runner_id = runner.get("id")
        if isinstance(runner_id, int):
            if runner_id in seen_ids:
                continue
            seen_ids.add(runner_id)
        unique.append(runner)
    return unique
