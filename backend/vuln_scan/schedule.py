"""Coverage and owner-enabled idle-capacity scheduling for security scans (#2574).

Coverage answers "which repositories are protected, and how recently?" for the
Security page. A repository is **due** when it is enabled in the operator
config, has a valid contract, and has no successful real scan within its
``full_scan_days``.

The scheduler is off by default (``schedule.enabled`` in
``config/security_scan.json``) and acts only on the hub (``MACHINE_ROLE``).
Each tick it re-reads the config, so turning it on or off takes effect without
a restart. Each tick:

1. does nothing unless the schedule is enabled, the night window is open and
   the single scan slot is free, so daytime ticks cost no GitHub calls;
2. takes the most urgent due repository (lowest ``priority``, then the oldest
   last success). A repository whose last real attempt failed or aborted is not
   due again for ``schedule.retry_hours``, so one broken contract cannot starve
   the rest;
3. evaluates admission **without** writing a row, so the many ticks outside the
   night window or while CI is busy leave no noise in the scan log;
4. starts a real scan only if admission has no objection.

All the productivity guards (VM host, idle CI, night window, one slot, budget,
disk) stay in :mod:`vuln_scan.admission`; the scheduler adds none of its own and
can bypass none of them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from vuln_scan.admission import in_window
from vuln_scan.operator_config import OperatorConfig, load_operator_config

log = logging.getLogger("dashboard.vuln_scan.schedule")
RETRY_STATES = ("failed", "aborted")


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _backing_off(scans: Mapping[str, Any], now: datetime, retry_hours: int) -> bool:
    """True while a failed or aborted last real attempt is younger than the backoff.

    Without it a repository whose scan keeps failing would stay first in line and
    take the single slot every night (panel q5).
    """
    if scans.get("last_state") not in RETRY_STATES:
        return False
    last = _parse(str(scans.get("last_scan_at", "")))
    return last is not None and now - last < timedelta(hours=retry_hours)


def coverage_rows(
    config: OperatorConfig,
    repos: list[str],
    contracts: Mapping[str, Mapping[str, Any]],
    latest: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """One coverage row per repository, most urgent due repository first."""
    retry_hours = config.schedule.retry_hours
    rows = []
    for repo in repos:
        settings = config.repo(repo)
        contract = contracts.get(repo, {})
        scans = latest.get(repo, {})
        last_success = _parse(str(scans.get("last_success_at", "")))
        stale = last_success is None or now - last_success > timedelta(
            days=settings.full_scan_days
        )
        valid = bool(contract.get("valid"))
        rows.append(
            {
                "repo": repo,
                "contract_present": bool(contract.get("present")),
                "contract_valid": valid,
                "contract_error": contract.get("error"),
                "enabled": settings.enabled,
                "priority": settings.priority,
                "full_scan_days": settings.full_scan_days,
                "last_scan_at": scans.get("last_scan_at", ""),
                "last_state": scans.get("last_state", ""),
                "last_success_at": scans.get("last_success_at", ""),
                "contract_disabled": bool(contract.get("disabled")),
                "due": settings.enabled
                and valid
                and not contract.get("disabled")
                and stale
                and not _backing_off(scans, now, retry_hours),
            }
        )

    def urgency(row: dict[str, Any]) -> tuple[bool, int, str]:
        return (not row["due"], row["priority"], row["last_success_at"])

    return sorted(rows, key=urgency)


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "repos": len(rows),
        "with_contract": sum(1 for r in rows if r["contract_present"]),
        "valid": sum(1 for r in rows if r["contract_valid"]),
        "enabled": sum(1 for r in rows if r["enabled"]),
        "scanned": sum(1 for r in rows if r["last_success_at"]),
        "due": sum(1 for r in rows if r["due"]),
    }


Planner = Callable[
    [str], Awaitable[tuple[list[str], Callable[[], Awaitable[dict[str, Any]]]]]
]


async def tick(
    *,
    config: OperatorConfig,
    slot_free: Callable[[], bool],
    coverage: Callable[[], Awaitable[list[dict[str, Any]]]],
    plan: Planner,
    now: datetime,
) -> dict[str, Any]:
    """One scheduler decision. ``plan(repo)`` returns (refusal reasons, start)."""
    if not config.schedule.enabled:
        return {"started": False, "reason": "schedule disabled"}
    if not in_window(now, config.window_start, config.window_end, config.timezone):
        return {"started": False, "reason": "outside the scan window"}
    if not slot_free():
        return {"started": False, "reason": "scan slot busy"}
    due = [row for row in await coverage() if row["due"]]
    if not due:
        return {"started": False, "reason": "nothing due"}
    repo = str(due[0]["repo"])
    reasons, start = await plan(repo)
    if reasons:
        return {"started": False, "repo": repo, "reasons": reasons}
    row = await start()
    return {"started": row.get("state") == "running", "repo": repo, "scan": row}


async def schedule_loop(
    run_tick: Callable[[], Awaitable[dict[str, Any]]],
    *,
    load: Callable[[], OperatorConfig] = load_operator_config,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Hub-only background loop; each pass re-reads the config, then sleeps."""
    while True:
        minutes = load().schedule.check_minutes
        try:
            result = await run_tick()
            if result.get("started") or result.get("reasons"):
                log.info("security scan scheduler: %s", result)
        except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
            log.exception("security scan scheduler tick failed")
        await sleep(minutes * 60)
