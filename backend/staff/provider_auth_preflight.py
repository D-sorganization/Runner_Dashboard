"""Evening provider OAuth probes for Barb's summary (#2170 follow-up)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from staff.cli_oauth_session import (
    oauth_expiry_epoch_for_provider,
    oauth_has_refresh_token_for_provider,
    oauth_last_refresh_epoch_for_provider,
    provider_oauth_session_alive,
)

log = logging.getLogger("dashboard.staff.provider_auth_preflight")

_EVENING_TZ = ZoneInfo("America/Los_Angeles")
_EVENING_HOUR = 20
_EXPIRING_HOURS = 48.0
_KEEPALIVE_INTERVAL_SECONDS = 4 * 3600
_STALE_REFRESH_DAYS = float(os.environ.get("STAFF_PROVIDER_AUTH_STALE_DAYS", "7"))
_PROBE_PROVIDERS = ("claude", "antigravity", "codex")

_cache: dict[str, Any] = {"checked_at": None, "providers": {}, "machine": ""}


def _skip_live_provider_auth_probe() -> bool:
    return os.environ.get("STAFF_E2E_HERMETIC", "").strip() == "1"


def provider_auth_cache_snapshot() -> dict[str, Any]:
    return dict(_cache)


def _classify(
    provider: str,
    alive: bool,
    detail: str,
    expires_at: float | None,
    last_refresh_at: float | None,
    has_refresh_token: bool = False,
) -> dict[str, Any]:
    now = time.time()
    row: dict[str, Any] = {
        "provider": provider,
        "detail": detail,
        "has_refresh_token": has_refresh_token,
    }
    if expires_at is not None:
        row["expires_at"] = datetime.fromtimestamp(expires_at, UTC).isoformat().replace("+00:00", "Z")
        row["hours_remaining"] = round(max(0.0, (expires_at - now) / 3600.0), 2)
    if last_refresh_at is not None:
        row["last_refresh_at"] = datetime.fromtimestamp(last_refresh_at, UTC).isoformat().replace("+00:00", "Z")
        row["days_since_refresh"] = round(max(0.0, (now - last_refresh_at) / 86400.0), 2)
    if not alive:
        row["status"] = "expired"
    elif last_refresh_at is not None and (now - last_refresh_at) >= _STALE_REFRESH_DAYS * 86400:
        row["status"] = "stale"
        row["detail"] = (
            f"{detail}; subscription token not refreshed in {int((now - last_refresh_at) // 86400)} days"
            if detail
            else f"subscription token not refreshed in {int((now - last_refresh_at) // 86400)} days"
        )
    elif expires_at is not None and (
        expires_at <= now or (not has_refresh_token and expires_at <= now + _EXPIRING_HOURS * 3600)
    ):
        row["status"] = "expiring"
    else:
        row["status"] = "ok"
    return row


def probe_local_provider_auth(machine: str = "") -> dict[str, Any]:
    """Probe OAuth seats for unattended providers on this node."""
    if _skip_live_provider_auth_probe():
        return {
            "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "machine": machine,
            "providers": {},
            "skipped": True,
        }
    providers: dict[str, Any] = {}
    for pid in _PROBE_PROVIDERS:
        alive, detail = provider_oauth_session_alive(pid)
        expires = oauth_expiry_epoch_for_provider(pid)
        refreshed = oauth_last_refresh_epoch_for_provider(pid)
        has_refresh = oauth_has_refresh_token_for_provider(pid)
        providers[pid] = _classify(pid, alive, detail, expires, refreshed, has_refresh_token=has_refresh)
    return {
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "machine": machine,
        "providers": providers,
    }


def run_preflight_and_cache(machine: str = "") -> dict[str, Any]:
    report = probe_local_provider_auth(machine)
    _cache.clear()
    _cache.update(report)
    bad = [p for p, row in report["providers"].items() if row.get("status") != "ok"]
    if bad:
        log.warning("staff provider auth preflight: %s on %s", ", ".join(bad), report.get("machine") or "local")
    return report


def attention_items_from_provider_auth(report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    machine = str(report.get("machine") or "local")
    for pid, row in (report.get("providers") or {}).items():
        status = row.get("status")
        if status not in ("expired", "expiring", "stale"):
            continue
        failure = {
            "expired": "auth_expired",
            "expiring": "auth_expiring",
            "stale": "auth_stale",
        }[status]
        items.append(
            {
                "id": f"provider-auth-{machine}-{pid}",
                "role": "hub",
                "provider": pid,
                "machine": machine,
                "status": "blocked" if status == "expired" else "attention",
                "failure_class": failure,
                "error": row.get("detail") or f"{pid} auth {status}",
                "remediation": row.get("remediation")
                or f"Refresh {pid} credentials on {machine} (see docs/runbooks/durable-provider-credentials.md).",
            }
        )
    return items


def aggregate_provider_auth(board: dict[str, Any]) -> dict[str, Any]:
    """Fleet-wide provider auth from per-node board payloads."""
    machines: dict[str, Any] = {}
    latest: str | None = None
    for name, node in (board.get("machines") or {}).items():
        snap = node.get("provider_auth")
        if not snap:
            continue
        machines[name] = snap
        checked = snap.get("checked_at")
        if checked and (latest is None or checked > latest):
            latest = checked
    return {"checked_at": latest, "machines": machines}


def _seconds_until_next_evening_pt() -> float:
    now = datetime.now(_EVENING_TZ)
    target = now.replace(hour=_EVENING_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return min(float(_KEEPALIVE_INTERVAL_SECONDS), max(30.0, (target - now).total_seconds()))


async def periodic_provider_auth_evening_loop(machine_loader: Any) -> None:
    """Run a full OAuth keepalive and preflight probe every 4 hours (and at ~20:00 PT)."""
    await asyncio.sleep(10)
    while True:
        try:
            machine = str(machine_loader() or "local")
            if _cache.get("checked_at") is None:
                await asyncio.to_thread(run_preflight_and_cache, machine)
        except Exception as exc:  # noqa: BLE001
            log.warning("staff provider auth preflight bootstrap failed: %s", exc)
        await asyncio.sleep(min(float(_KEEPALIVE_INTERVAL_SECONDS), _seconds_until_next_evening_pt()))
        try:
            machine = str(machine_loader() or "local")
            await asyncio.to_thread(run_preflight_and_cache, machine)
        except Exception as exc:  # noqa: BLE001
            log.warning("staff provider auth evening preflight failed: %s", exc)
