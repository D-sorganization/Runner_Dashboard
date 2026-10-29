"""Dashboard agent-bus hook helpers (#2155 slice 2).

Session start: register (or reuse token), fetch inbox. Pre-push: heartbeat + inbox peek.
When the dashboard is unreachable, returns quietly so agents keep working offline.
Tokens are persisted under the identity dir only; never logged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_fleet_dir = Path(__file__).resolve().parents[2] / "clients" / "fleet"
if str(_fleet_dir) not in sys.path:
    sys.path.insert(0, str(_fleet_dir))

from activity_producers import emit_activity  # noqa: E402
from fleet_client import FleetAPIError, FleetClient  # noqa: E402
from fleet_validators import short_host  # noqa: E402

log = logging.getLogger("dashboard.agent_bus.session_hooks")

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


def _resolve_role(client: Any, explicit: str | None) -> str:
    if explicit:
        return explicit
    if client.agent:
        return client.agent
    from fleet_provider_identity import infer_fleet_agent  # noqa: PLC0415

    inferred = infer_fleet_agent()
    if inferred:
        return inferred
    raise ValueError("role is required (FLEET_AGENT or argument)")


def _provider() -> str:
    from fleet_provider_identity import infer_provider_label  # noqa: PLC0415

    return infer_provider_label()


def _node(short_host: Any) -> str:
    return (os.environ.get("FLEET_NODE") or short_host()).strip()[:128]


def _offline_summary(exc: Any) -> dict[str, Any]:
    log.debug("agent bus offline: status=%s", exc.status)
    return {"offline": True, "registered": False, "inbox_count": 0}


def session_start(
    client: Any,
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
                node=node or _node(short_host),
                lease_seconds=lease_seconds,
            )
            minted = reg.get("token") if isinstance(reg, dict) else None
            if isinstance(minted, str) and minted:
                client.token = minted
                _save_state(session, minted, resolved_role)
        try:
            inbox = client.agent_inbox(session=session, since_cursor=0, limit=50)
        except FleetAPIError as exc:
            if not token or exc.status not in (401, 403):
                raise
            # Cached token was minted by a dashboard process that has since
            # restarted; ephemeral tokens are in-memory only, so the server no
            # longer recognizes it. Drop it and register fresh instead of
            # crashing the caller.
            log.warning("agent bus session start: cached token rejected (status=%s); re-registering", exc.status)
            client.token = None
            reg = client.agent_register(
                session=session,
                role=resolved_role,
                provider=provider or _provider(),
                node=node or _node(short_host),
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
        summary: dict[str, Any] = {"offline": False, "registered": True, "inbox_count": count, "session": session}
        try:
            from agent_bus.skills_delivery import export_skills_at_session_start

            summary.update(
                export_skills_at_session_start(
                    client,
                    role=resolved_role,
                    provider=provider or _provider(),
                )
            )
        except Exception:  # noqa: BLE001
            log.debug("skills export at session start failed", exc_info=True)
        emit_activity(
            source="agent_bus",
            session_id=session,
            node=node or _node(short_host),
            kind="session_start",
            payload={"role": resolved_role, "inbox_count": count},
        )
        return summary
    except FleetAPIError as exc:
        if exc.status == 0:
            return _offline_summary(exc)
        raise


def pre_push(client: Any | None = None, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> dict[str, Any]:
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
        emit_activity(
            source="agent_bus",
            session_id=str(state["session"]),
            node=_node(short_host),
            kind="pre_push_heartbeat",
            payload={"inbox_count": count},
        )
        return {"heartbeat": True, "inbox_count": count, "offline": False}
    except FleetAPIError as exc:
        if exc.status == 0:
            return _offline_summary(exc)
        if exc.status in (401, 403):
            # No role/provider is available here to re-register; clear the
            # stale state so the next session_start registers fresh instead
            # of this hook crashing on every push.
            log.warning("agent bus pre-push: cached token rejected (status=%s); clearing stale state", exc.status)
            try:
                state_file_path().unlink()
            except OSError:
                pass
            return {"heartbeat": False, "offline": False, "stale_token": True}
        raise
