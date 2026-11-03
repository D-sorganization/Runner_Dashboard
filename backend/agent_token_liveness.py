"""Agent token liveness alert: a missing or rejected token file (#3895 slice 4).

Runs every five minutes on the hub. A token file that is present but rejected
alerts at once; its rejection reason is what the API's bearer check returns as a
401 for that principal's own reads, since both go through
``IdentityManager.resolve_bearer_token``. A missing file alerts only for a
principal provisioned on this host: one whose file was seen here before (kept in
the state file across restarts) or that has a token minted for
``agent-tokens/<id>.token``. A principal configured for another host stays quiet.

Each outage alerts once; a change of failure reason alerts again, and recovery
closes the outage so the next one alerts. No alert or state entry carries a token.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_token_expiry import STATE_FILENAME as EXPIRY_STATE_FILENAME
from agent_token_expiry import check_token_expiry, emit_expiry_warning
from agent_token_health import token_health_report
from agent_token_rotation import check_token_rotation

if TYPE_CHECKING:
    from identity import IdentityManager

log = logging.getLogger("dashboard.agent_token_liveness")

LIVENESS_INTERVAL_SECONDS = 300.0
STATE_FILENAME = "agent_token_liveness.json"


def liveness_alerts(
    report: list[dict[str, Any]], *, known: set[str], open_alerts: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """New alerts for failing principals, and the open outages after this check."""
    alerts: list[dict[str, Any]] = []
    still_open: dict[str, str] = {}
    for row in report:
        principal = str(row["principal"])
        if row.get("valid") or (not row.get("present") and principal not in known):
            continue
        reason = str(row.get("reason") or "unknown")
        still_open[principal] = reason
        if open_alerts.get(principal) == reason:
            continue
        what = "missing" if not row.get("present") else "rejected"
        alerts.append(
            {
                "principal": principal,
                "severity": "critical",
                "title": f"Agent token for {principal} is {what}",
                "detail": (
                    f"The token file for {principal} is {what} ({reason}); its reads will get 401 until "
                    "it is re-minted. See docs/agents/connect.md."
                ),
            }
        )
    return alerts, still_open


def _load_state(path: Path) -> tuple[set[str], dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), {}
    except (OSError, ValueError):
        log.warning("agent token liveness state %s unreadable; starting fresh", path.name)
        return set(), {}
    if not isinstance(data, dict):
        return set(), {}
    known = {str(p) for p in data.get("known") or []}
    raw_open = data.get("open")
    open_alerts = raw_open if isinstance(raw_open, dict) else {}
    return known, {str(k): str(v) for k, v in open_alerts.items()}


def _minted_for_file(manager: IdentityManager) -> set[str]:
    return {t.principal_id for t in manager.tokens if t.name == f"agent-tokens/{t.principal_id}.token"}


def check_token_liveness(
    manager: IdentityManager,
    *,
    state_path: Path,
    emit: Callable[[dict[str, Any]], None] = emit_expiry_warning,
) -> list[dict[str, Any]]:
    """Emit one alert per new outage and record state; returns the alerts sent."""
    report = token_health_report(manager)
    known, open_alerts = _load_state(state_path)
    known |= _minted_for_file(manager) | {str(r["principal"]) for r in report if r.get("present")}
    alerts, open_alerts = liveness_alerts(report, known=known, open_alerts=open_alerts)
    for alert in alerts:
        emit(alert)
        log.warning("agent token liveness alert: %s", alert["title"])
    state_path.write_text(json.dumps({"known": sorted(known), "open": open_alerts}), encoding="utf-8")
    return alerts


async def periodic_agent_token_loop(interval: float = LIVENESS_INTERVAL_SECONDS) -> None:
    """Hub background task: liveness and rotation every ``interval`` seconds, plus debounced expiry warnings."""
    from identity import identity_manager  # noqa: PLC0415

    config_dir = identity_manager.config_dir
    while True:
        for check, state in (
            (check_token_liveness, config_dir / STATE_FILENAME),
            (check_token_expiry, config_dir / EXPIRY_STATE_FILENAME),
        ):
            try:
                await asyncio.to_thread(check, identity_manager, state_path=state)
            except Exception:  # noqa: BLE001 - the loop must outlive one bad check
                log.exception("agent token check %s failed", check.__name__)
        try:
            await asyncio.to_thread(check_token_rotation, identity_manager)
        except Exception:  # noqa: BLE001 - the loop must outlive one bad check
            log.exception("agent token check %s failed", check_token_rotation.__name__)
        await asyncio.sleep(interval)
