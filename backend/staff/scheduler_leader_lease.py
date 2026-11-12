"""Multi-host leader-lease fencing for the staff scheduler (500-line cap split).

Extracted from ``staff.scheduler`` so at most one host in
``STAFF_SCHEDULER_LEADER_CHAIN`` fires scheduled slots at a time. All functions
here are stateless helpers that take the scheduler's state accessors
(``load_state``/``save_state``) or a bound ``acquire_local`` callback so the
caller keeps its own locking.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from dashboard_config import HUB_URL
from machine_registry import is_portable_machine

log = logging.getLogger("dashboard.staff.scheduler")

LEADER_LEASE_STATE_KEY = "__leader_lease__"
LEADER_CHAIN_ENV = "STAFF_SCHEDULER_LEADER_CHAIN"
LEADER_TTL_SECONDS_ENV = "STAFF_SCHEDULER_LEASE_TTL_SECONDS"
LEADER_AUTHORITY_URL_ENV = "STAFF_SCHEDULER_LEASE_AUTHORITY_URL"
LEADER_HTTP_TIMEOUT_SECONDS = 5.0


def iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


def parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def leader_chain() -> tuple[str, ...]:
    raw = os.environ.get(LEADER_CHAIN_ENV, "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def lease_ttl_seconds(tick_seconds: float) -> float:
    raw = os.environ.get(LEADER_TTL_SECONDS_ENV, "").strip()
    if raw:
        try:
            ttl = float(raw)
            if ttl > 0:
                return ttl
        except ValueError:
            log.warning("staff scheduler: ignored bad %s=%r", LEADER_TTL_SECONDS_ENV, raw)
    return max(tick_seconds * 3, tick_seconds + 1)


def lease_authority_url() -> str:
    raw = os.environ.get(LEADER_AUTHORITY_URL_ENV, "").strip()
    if raw:
        return raw.rstrip("/")
    return (HUB_URL or "").rstrip("/")


def acquire_leader_lease(
    load_state: Callable[[], dict[str, Any]],
    save_state: Callable[[], None],
    machine: str,
    *,
    now: datetime,
    ttl_seconds: float,
) -> dict[str, Any]:
    """Acquire/renew the scheduler lease on the local authority state file.

    Precondition: ``machine`` is a non-empty fleet display name; the caller holds
    whatever lock guards ``load_state``/``save_state``.
    Postcondition: result includes ``granted`` and current ``holder``.
    """
    if is_portable_machine(machine):
        return {
            "granted": False,
            "holder": "",
            "reason": f"{machine} is a portable node: no required or scheduled roles (#3323)",
            "expires_at": None,
            "chain": list(leader_chain()),
        }
    chain = leader_chain()
    if not chain:
        return {
            "granted": True,
            "holder": machine,
            "reason": "leader lease disabled",
            "expires_at": None,
            "chain": [],
        }
    if machine not in chain:
        return {
            "granted": False,
            "holder": "",
            "reason": f"{machine} not in leader chain",
            "expires_at": None,
            "chain": list(chain),
        }
    state = load_state()
    entry = state.setdefault(LEADER_LEASE_STATE_KEY, {})
    if not isinstance(entry, dict):
        entry = {}
        state[LEADER_LEASE_STATE_KEY] = entry
    holder = str(entry.get("holder") or "")
    expires_at = parse_iso(str(entry.get("expires_at") or ""))
    acquired_at = str(entry.get("acquired_at") or "")
    ranks = {name: idx for idx, name in enumerate(chain)}
    holder_rank = ranks.get(holder, 10_000)
    requester_rank = ranks.get(machine, 10_000)
    active = expires_at is not None and expires_at > now
    granted = False
    reason = ""
    if holder == machine:
        granted = True
        reason = "renewed"
    elif (not active) or not holder:
        granted = True
        reason = "acquired"
    elif requester_rank < holder_rank:
        granted = True
        reason = "preempted by higher-priority host"
    else:
        reason = f"held by {holder}"

    if granted:
        expires = now + timedelta(seconds=ttl_seconds)
        entry["holder"] = machine
        entry["acquired_at"] = acquired_at or now.isoformat()
        if holder != machine:
            entry["acquired_at"] = now.isoformat()
        entry["renewed_at"] = now.isoformat()
        entry["expires_at"] = expires.isoformat()
        entry["ttl_seconds"] = ttl_seconds
        entry["chain"] = list(chain)
        save_state()
        holder = machine
        expires_at = expires
    return {
        "granted": granted,
        "holder": holder,
        "reason": reason,
        "expires_at": iso(expires_at),
        "chain": list(chain),
    }


def acquire_remote_leader_lease(
    acquire_local: Callable[..., dict[str, Any]],
    machine: str,
    now: datetime,
    ttl_seconds: float,
) -> dict[str, Any]:
    """Ask the lease authority (HUB_URL by default) to grant the lease, falling back to the
    local state file when no authority is configured."""
    authority = lease_authority_url()
    if not authority:
        return acquire_local(machine, now=now, ttl_seconds=ttl_seconds)
    body = json.dumps({"machine": machine, "ttl_seconds": ttl_seconds}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    token = os.environ.get("HUB_FLEET_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(f"{authority}/api/staff/schedule/leader-lease", data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=LEADER_HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        return {
            "granted": False,
            "holder": "",
            "reason": f"leader lease authority unavailable: {exc}",
            "expires_at": None,
            "chain": list(leader_chain()),
        }
    if not isinstance(payload, dict):
        return {
            "granted": False,
            "holder": "",
            "reason": "leader lease authority returned non-object payload",
            "expires_at": None,
            "chain": list(leader_chain()),
        }
    return payload


def lease_allows_firing(
    acquire_local: Callable[..., dict[str, Any]],
    runner_machine: str,
    now: datetime,
    ttl_seconds: float,
) -> bool:
    if is_portable_machine(runner_machine):
        return False
    chain = leader_chain()
    if not chain:
        return True
    if runner_machine not in chain:
        return False
    decision = acquire_remote_leader_lease(acquire_local, runner_machine, now, ttl_seconds)
    if not bool(decision.get("granted", False)):
        holder = str(decision.get("holder") or "<none>")
        reason = str(decision.get("reason") or "lease denied")
        log.info("staff scheduler: lease denied on %s (holder=%s) - %s", runner_machine, holder, reason)
        return False
    return True


def leader_status(
    load_state: Callable[[], dict[str, Any]],
    chain: tuple[str, ...],
    holder_machine: str,
    now: datetime,
) -> dict[str, Any]:
    entry = load_state().get(LEADER_LEASE_STATE_KEY, {})
    if not isinstance(entry, dict):
        entry = {}
    holder = str(entry.get("holder") or "")
    expires_at = parse_iso(str(entry.get("expires_at") or ""))
    active = bool(holder) and expires_at is not None and expires_at > now
    return {
        "configured": bool(chain),
        "chain": list(chain),
        "holder": holder,
        "acquired_at": str(entry.get("acquired_at") or ""),
        "renewed_at": str(entry.get("renewed_at") or ""),
        "expires_at": iso(expires_at),
        "active": active,
        "local_holder": active and holder == holder_machine,
    }
