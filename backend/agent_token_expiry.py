"""Agent token expiry warnings at 14 and 3 days before expiry (#3895 slice 2).

Built on :func:`agent_token_health.token_health_report`. Each threshold alerts
once per token (keyed by principal, threshold and the token's ``expires_at``, so
a rotated token re-arms both). Sent keys persist in a small JSON state file so a
restart does not repeat a warning. Expired or rejected tokens are left to the
liveness alert (slice 4). No alert, log line or state entry carries a token.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_token_health import token_health_report

if TYPE_CHECKING:
    from identity import IdentityManager

log = logging.getLogger("dashboard.agent_token_expiry")

WARN_THRESHOLDS_DAYS = (14, 3)
STATE_FILENAME = "agent_token_expiry_warnings.json"


def due_warnings(report: list[dict[str, Any]], sent: set[str]) -> list[dict[str, Any]]:
    """Warnings owed for live tokens inside a threshold whose key is not in ``sent``.

    Only the tightest crossed threshold is considered, so a token first seen at
    two days left raises the 3-day warning alone.
    """
    alerts: list[dict[str, Any]] = []
    for row in report:
        days = row.get("days_to_expiry")
        if not row.get("valid") or days is None or days <= 0:
            continue
        crossed = [t for t in WARN_THRESHOLDS_DAYS if days <= t]
        if not crossed:
            continue
        threshold = min(crossed)
        principal = str(row["principal"])
        key = f"{principal}:{threshold}:{int(row['expires_at'])}"
        if key in sent:
            continue
        expires = datetime.fromtimestamp(float(row["expires_at"]), UTC).strftime("%Y-%m-%d %H:%MZ")
        alerts.append(
            {
                "key": key,
                "principal": principal,
                "threshold_days": threshold,
                "days_to_expiry": days,
                "title": f"Agent token for {principal} expires in {days:.1f} days",
                "detail": (
                    f"The bearer token for {principal} expires at {expires} (inside the {threshold}-day "
                    "warning). Re-mint it before then; see docs/agents/connect.md."
                ),
            }
        )
    return alerts


def emit_expiry_warning(alert: dict[str, Any]) -> None:
    """Send one alert to the fleet event log, the escalation push topic and the sentinel Barb inbox.

    Severity comes from ``alert["severity"]`` (default ``warning``); the liveness
    alert (slice 4) reuses this path with ``critical``.
    """
    import push  # noqa: PLC0415
    from fleet_events import FleetEvent, get_event_store  # noqa: PLC0415

    get_event_store().record(
        FleetEvent(
            ts=int(time.time() * 1000),
            severity=alert.get("severity", "warning"),
            kind="watchdog",
            title=alert["title"],
            detail=alert["detail"],
        )
    )
    push.notify("staff.escalation", alert["title"], alert["detail"])
    inbox = os.environ.get("HUB_SENTINEL_BARB_INBOX", "").strip()
    if inbox:
        path = Path(inbox).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(UTC).isoformat()} {alert['title']}: {alert['detail']}\n")


def _load_sent(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, ValueError):
        log.warning("agent token expiry state %s unreadable; starting fresh", path.name)
        return set()
    return {str(k) for k in data} if isinstance(data, list) else set()


def check_token_expiry(
    manager: IdentityManager,
    *,
    state_path: Path,
    emit: Callable[[dict[str, Any]], None] = emit_expiry_warning,
) -> list[dict[str, Any]]:
    """Emit every owed warning once and record it; returns the warnings sent."""
    sent = _load_sent(state_path)
    alerts = due_warnings(token_health_report(manager), sent)
    for alert in alerts:
        emit(alert)
        sent.add(alert["key"])
        log.warning("agent token expiry warning: %s", alert["title"])
    if alerts:
        state_path.write_text(json.dumps(sorted(sent)), encoding="utf-8")
    return alerts
