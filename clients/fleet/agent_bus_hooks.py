"""Dashboard agent-bus hook helpers (#2155 slice 2).

Session start: register (or reuse token), fetch inbox. Pre-push: heartbeat + inbox peek.
When the dashboard is unreachable, returns quietly so agents keep working offline.
Tokens are persisted under the identity dir only; never logged.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fleet_client import FleetAPIError, FleetClient
from fleet_validators import short_host

log = logging.getLogger("fleet.agent_bus_hooks")

DEFAULT_LEASE_SECONDS = 300


def _identity_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (base / "runner-dashboard").expanduser()


def state_file_path() -> Path:
    override = os.environ.get("AGENT_BUS_STATE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _identity_dir() / "agent-bus-state.json"


def _load_state() -> dict[str, Any] | None:
    path = state_file_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_state(session: str, token: str, role: str) -> None:
    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session": session, "token": token, "role": role}, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _resolve_role(client: FleetClient, explicit: str | None) -> str:
    if explicit:
        return explicit
    if client.agent:
        return client.agent
    raise ValueError("role is required (FLEET_AGENT or argument)")


def _provider() -> str:
    return (os.environ.get("FLEET_PROVIDER") or os.environ.get("CURSOR_AGENT") or "unknown").strip()[:64]


def _node() -> str:
    return (os.environ.get("FLEET_NODE") or short_host()).strip()[:128]


def _offline_summary(exc: FleetAPIError) -> dict[str, Any]:
    log.debug("agent bus offline: status=%s", exc.status)
    return {"offline": True, "registered": False, "inbox_count": 0}


def session_start(
    client: FleetClient,
    *,
    role: str | None = None,
    provider: str | None = None,
    node: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    """Register this session and fetch inbox; no-op when the dashboard is down."""
    session = client.session
    if not session:
        raise ValueError("session is required on the FleetClient")
    resolved_role = _resolve_role(client, role)
    state = _load_state()
    token = (state or {}).get("token") if state and state.get("session") == session else None
    if token:
        client.token = str(token)
    try:
        if not token:
            reg = client.agent_register(
                session=session,
                role=resolved_role,
                provider=provider or _provider(),
                node=node or _node(),
                lease_seconds=lease_seconds,
            )
            minted = reg.get("token") if isinstance(reg, dict) else None
            if isinstance(minted, str) and minted:
                client.token = minted
                _save_state(session, minted, resolved_role)
        inbox = client.agent_inbox(session=session, since_cursor=0, limit=50)
        messages = inbox.get("messages", []) if isinstance(inbox, dict) else []
        count = len(messages) if isinstance(messages, list) else 0
        log.info("agent bus session start: session=%s inbox=%d", session, count)
        return {"offline": False, "registered": True, "inbox_count": count, "session": session}
    except FleetAPIError as exc:
        if exc.status == 0:
            return _offline_summary(exc)
        raise


def pre_push(client: FleetClient | None = None, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> dict[str, Any]:
    """Heartbeat and refresh inbox cursor; no-op when offline or no saved session."""
    state = _load_state()
    if not state or not state.get("session") or not state.get("token"):
        return {"skipped": True, "reason": "no agent-bus state"}
    fc = client or FleetClient(token=str(state["token"]), session=str(state["session"]))
    try:
        fc.agent_heartbeat(session=str(state["session"]), lease_seconds=lease_seconds)
        inbox = fc.agent_inbox(session=str(state["session"]), since_cursor=0, limit=20)
        messages = inbox.get("messages", []) if isinstance(inbox, dict) else []
        count = len(messages) if isinstance(messages, list) else 0
        log.info("agent bus pre-push: session=%s inbox=%d", state["session"], count)
        return {"heartbeat": True, "inbox_count": count, "offline": False}
    except FleetAPIError as exc:
        if exc.status == 0:
            return _offline_summary(exc)
        raise
