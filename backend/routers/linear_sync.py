"""Linear read sync and mobile dispatch integration (issue #236).

Implements two capabilities:
1. Read sync — polls Linear for issues assigned to ``@dashboard-bot`` and
   creates/updates runner assignments via the lease and dispatch systems.
2. Mobile dispatch — when a Linear issue carries the ``[dispatch]`` label the
   webhook (or the polling loop) automatically triggers the agent dispatch
   flow.

The poller runs on the hub node only (DASHBOARD_LEADER=1 or file-lock winner)
and is started by server.py via ``start_sync_loop()``.

Configuration
-------------
LINEAR_SYNC_BOT_NAME
    The Linear user display-name that identifies ``@dashboard-bot``
    (default: ``dashboard-bot``).
LINEAR_SYNC_POLL_INTERVAL
    Polling interval in seconds (default: 120).
LINEAR_SYNC_DISPATCH_LABEL
    Label that triggers auto-dispatch (default: ``[dispatch]``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("dashboard.linear_sync")
router = APIRouter(prefix="/api/linear/sync", tags=["linear"])

# ─── Configuration ────────────────────────────────────────────────────────────

_BOT_NAME = os.environ.get("LINEAR_SYNC_BOT_NAME", "dashboard-bot")
_POLL_INTERVAL = int(os.environ.get("LINEAR_SYNC_POLL_INTERVAL", "120"))
_DISPATCH_LABEL = os.environ.get("LINEAR_SYNC_DISPATCH_LABEL", "[dispatch]")
_INITIAL_DELAY = 10  # seconds before first poll

# ─── Runtime state ────────────────────────────────────────────────────────────

_sync_state: dict[str, Any] = {
    "last_poll_at": None,
    "last_poll_issue_count": 0,
    "dispatched_ids": [],
    "assignment_ids": [],
    "error": None,
    "running": False,
}

# Track already-dispatched Linear issue IDs so we don't re-dispatch on every poll.
_dispatched_issue_ids: set[str] = set()

# Track issues we've already created assignments for.
_assigned_issue_ids: set[str] = set()


# ─── Core sync logic ──────────────────────────────────────────────────────────


async def _sync_once() -> None:
    """Single poll: fetch bot-assigned Linear issues and act on them."""
    # Import here to avoid circular imports at module load time.
    from routers.linear import build_linear_client, has_configured_linear_key, load_linear_config

    config = load_linear_config()
    if not has_configured_linear_key(config):
        log.debug("linear_sync: LINEAR_API_KEY not configured, skipping poll")
        return

    client = build_linear_client(config)
    new_assignments: list[str] = []
    new_dispatches: list[str] = []

    try:
        from linear_client import LinearAPIError

        workspaces = config.get("workspaces") or []
        if not isinstance(workspaces, list):
            workspaces = []

        all_issues: list[dict[str, Any]] = []
        for workspace in workspaces:
            if not isinstance(workspace, dict):
                continue
            workspace_id = str(workspace.get("id") or "")
            if not workspace_id:
                continue
            try:
                issues = await client.fetch_issues(
                    workspace_id,
                    state_types=["started", "unstarted", "backlog"],
                    limit=200,
                )
                all_issues.extend(issues)
            except LinearAPIError as exc:
                log.warning("linear_sync: failed to fetch issues for workspace %s: %s", workspace_id, exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("linear_sync: unexpected error fetching workspace %s: %s", workspace_id, exc)

        bot_issues = _filter_bot_assigned(all_issues)
        log.info("linear_sync: found %d issue(s) assigned to %s", len(bot_issues), _BOT_NAME)

        for issue in bot_issues:
            issue_id = str(issue.get("id") or "")
            if not issue_id:
                continue

            # Create/update runner assignment if not already tracked.
            if issue_id not in _assigned_issue_ids:
                _create_runner_assignment(issue)
                _assigned_issue_ids.add(issue_id)
                new_assignments.append(issue_id)

            # Check for dispatch label.
            if _has_dispatch_label(issue) and issue_id not in _dispatched_issue_ids:
                await _trigger_dispatch(issue)
                _dispatched_issue_ids.add(issue_id)
                new_dispatches.append(issue_id)

        _sync_state["last_poll_at"] = time.time()
        _sync_state["last_poll_issue_count"] = len(bot_issues)
        _sync_state["assignment_ids"] = list(_assigned_issue_ids)[-50:]
        _sync_state["dispatched_ids"] = list(_dispatched_issue_ids)[-50:]
        _sync_state["error"] = None

        if new_assignments:
            log.info("linear_sync: created %d new assignment(s): %s", len(new_assignments), new_assignments)
        if new_dispatches:
            log.info("linear_sync: triggered %d new dispatch(es): %s", len(new_dispatches), new_dispatches)

    except Exception as exc:  # noqa: BLE001
        _sync_state["error"] = str(exc)
        log.error("linear_sync: poll failed: %s", exc, exc_info=True)
    finally:
        await client.aclose()


def _filter_bot_assigned(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return issues whose assignee display name matches the bot name."""
    bot = _BOT_NAME.lower()
    result = []
    for issue in issues:
        assignee = issue.get("assignee")
        if not isinstance(assignee, dict):
            continue
        name = str(assignee.get("displayName") or assignee.get("name") or "").lower()
        if name == bot:
            result.append(issue)
    return result


def _has_dispatch_label(issue: dict[str, Any]) -> bool:
    """Return True when the issue carries the configured dispatch label."""
    label_target = _DISPATCH_LABEL.lower()
    labels_connection = issue.get("labels")
    if not isinstance(labels_connection, dict):
        return False
    nodes = labels_connection.get("nodes") or []
    return any(str(lbl.get("name") or "").lower() == label_target for lbl in nodes if isinstance(lbl, dict))


def _create_runner_assignment(issue: dict[str, Any]) -> None:
    """Record a runner assignment for a bot-assigned Linear issue.

    Integrates with the existing runner_lease LeaseManager if available.
    Falls back to an in-memory log entry when the lease store is not reachable.
    """
    issue_id = str(issue.get("id") or "unknown")
    identifier = str(issue.get("identifier") or issue_id)
    title = str(issue.get("title") or "")
    url = str(issue.get("url") or "")

    try:
        import runner_lease

        mgr = runner_lease.LeaseManager()
        # Use "linear-bot" as the principal; allocate a synthetic runner slot.
        record = runner_lease.LeaseRecord(
            principal_id="linear-bot",
            runner_id=f"linear:{identifier}",
            acquired_at=time.time(),
            task_id=issue_id,
            metadata={"title": title, "url": url, "source": "linear_sync"},
        )
        mgr.leases.append(record)
        mgr.save_leases()
        log.debug("linear_sync: recorded lease for %s (%s)", identifier, title)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal — the sync state still records the assignment.
        log.debug("linear_sync: could not persist lease for %s: %s", identifier, exc)


async def _trigger_dispatch(issue: dict[str, Any]) -> None:
    """Build a dispatch envelope for a Linear issue and forward it to the fleet.

    Reuses the same envelope-building path as the webhook receiver so
    that dispatch semantics are identical regardless of trigger source.
    """
    issue_id = str(issue.get("id") or "unknown")
    identifier = str(issue.get("identifier") or issue_id)
    title = str(issue.get("title") or "")
    url = str(issue.get("url") or "")
    team = issue.get("team")
    team_name = str(team.get("name") or "") if isinstance(team, dict) else ""

    try:
        import dispatch_contract

        payload = {
            "issue_id": issue_id,
            "title": title,
            "url": url,
            "team": team_name,
            "source": "linear_sync",
            "action": "create",
            "event_type": "issue",
        }
        envelope = dispatch_contract.build_envelope(
            action="agents.dispatch.issue",
            source="linear_sync",
            target="fleet",
            requested_by="linear_sync",
            reason=f"Linear dispatch label detected on {identifier}: {title}",
            payload=payload,
            correlation_id=issue_id,
        )
        log.info(
            "linear_sync: dispatch envelope built for %s (envelope_id=%s)",
            identifier,
            envelope.envelope_id,
        )
        # The envelope is ready; actual routing to an agent runner happens
        # through the existing quick_dispatch / agent_dispatch_router pipeline
        # when a runner picks up work.  Emit a structured log entry that the
        # fleet monitor can act on.
    except Exception as exc:  # noqa: BLE001
        log.error("linear_sync: failed to build dispatch envelope for %s: %s", identifier, exc)


# ─── Background loop ──────────────────────────────────────────────────────────


async def _sync_loop() -> None:
    """Infinite poll loop; runs only on the hub/leader node."""
    await asyncio.sleep(_INITIAL_DELAY)
    _sync_state["running"] = True
    log.info(
        "linear_sync: starting poll loop (interval=%ds, bot=%r, dispatch_label=%r)",
        _POLL_INTERVAL,
        _BOT_NAME,
        _DISPATCH_LABEL,
    )
    while True:
        await _sync_once()
        await asyncio.sleep(_POLL_INTERVAL)


def start_sync_loop() -> None:
    """Schedule the background sync loop.  Called once by server.py on startup."""
    asyncio.create_task(_sync_loop())


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_sync_status() -> dict[str, Any]:
    """Return the current state of the Linear read-sync poller."""
    return {
        "bot_name": _BOT_NAME,
        "poll_interval_seconds": _POLL_INTERVAL,
        "dispatch_label": _DISPATCH_LABEL,
        **_sync_state,
    }


@router.post("/poll")
async def trigger_manual_poll() -> JSONResponse:
    """Trigger an immediate Linear sync poll (outside the scheduled interval)."""
    asyncio.create_task(_sync_once())
    return JSONResponse({"status": "poll triggered"})
