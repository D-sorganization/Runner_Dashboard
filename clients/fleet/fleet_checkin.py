"""Unconnected-tier check-in client with bounded offline spool (#2875, epic #2856 slice R3)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fleet_validators import (
    PATTERNS,
    FleetAPIError,
    FleetArgumentError,
    _check,
    _match,
    _non_negative_int,
    _opt,
    _opt_text,
    _positive_int,
    short_host,
)

CHECKIN_PATH = "/api/v1/activity/checkins"
CHECKIN_KINDS: tuple[str, ...] = ("started", "claimed", "pushed", "pr_opened", "blocked", "done", "heartbeat")
_CHECKIN_REQUIRES: dict[str, str] = {"claimed": "issue", "pushed": "branch", "pr_opened": "pr"}
CHECKIN_BATCH_EVENTS = 20
CHECKIN_BATCH_BYTES = 65_536
MAX_PAYLOAD_BYTES = 16_384
DEFAULT_MAX_SPOOL_EVENTS = 200
DEFAULT_MAX_SPOOL_BYTES = 262_144
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF = 0.2
_RETRYABLE_STATUSES = frozenset({0, 429, 502, 503, 504})


def default_spool_path(explicit: str | Path | None = None) -> Path:
    """Resolve the offline check-in spool path (DbC: non-empty path)."""
    raw = str(explicit or os.environ.get("FLEET_CHECKIN_SPOOL") or "").strip()
    if raw:
        return Path(raw).expanduser()
    cfg = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR", "").strip()
    if cfg:
        return Path(cfg).expanduser() / "checkin-spool.json"
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return xdg / "runner-dashboard" / "checkin-spool.json"


def _load_spool(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "sequences": {}, "queue": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetArgumentError(f"corrupt checkin spool file {path}: {exc}") from exc
    _check(isinstance(data, dict) and data.get("schema_version") == 1, "unsupported checkin spool schema_version")
    sequences, queue = data.get("sequences"), data.get("queue")
    _check(isinstance(sequences, dict) and isinstance(queue, list), "malformed checkin spool structure")
    return {"schema_version": 1, "sequences": dict(sequences), "queue": list(queue)}


def _save_spool(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _bound_queue(queue: list[dict[str, Any]], max_events: int, max_bytes: int) -> tuple[list[dict[str, Any]], int]:
    """Evict oldest queued envelopes so the offline spool stays within event and byte caps."""
    dropped = 0
    while len(queue) > max_events or (
        len(queue) > 1 and len(json.dumps(queue, separators=(",", ":")).encode("utf-8")) > max_bytes
    ):
        queue.pop(0)
        dropped += 1
    return queue, dropped


def _next_batch(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    batch: list[dict[str, Any]] = []
    for item in queue[:CHECKIN_BATCH_EVENTS]:
        candidate = [*batch, item]
        if batch and len(json.dumps(candidate, separators=(",", ":")).encode("utf-8")) > CHECKIN_BATCH_BYTES:
            break
        batch = candidate
    return batch


class CheckinClientMixin:
    """Offline-spooled ``checkin`` mixed into :class:`fleet_client.FleetClient`."""

    agent: str | None
    timeout: float

    def request(self, method: str, path: str, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def _session(self, session: str | None, agent: str | None = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def _agent(self, agent: str | None) -> str | None:  # pragma: no cover
        raise NotImplementedError

    def checkin(
        self,
        kind: str,
        repo: str,
        *,
        issue: int | None = None,
        branch: str | None = None,
        pr: int | None = None,
        note: str | None = None,
        payload: dict[str, Any] | None = None,
        node: str | None = None,
        principal: str | None = None,
        session: str | None = None,
        agent: str | None = None,
        seq: int | None = None,
        spool_file: str | Path | None = None,
        max_spool_events: int = DEFAULT_MAX_SPOOL_EVENTS,
        max_spool_bytes: int = DEFAULT_MAX_SPOOL_BYTES,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_backoff: float = DEFAULT_BASE_BACKOFF,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any]:
        """Record a check-in event and flush pending offline spool items with backoff."""
        _check(kind in CHECKIN_KINDS, f"unknown checkin kind: {kind!r} (expected one of {CHECKIN_KINDS})")
        checked_repo = _match(PATTERNS.repo, repo, "repo")
        checked_issue = None if issue is None else _positive_int(issue, "issue")
        checked_branch = _opt(PATTERNS.branch, branch, "branch")
        checked_pr = None if pr is None else _positive_int(pr, "pr")
        required = _CHECKIN_REQUIRES.get(kind)
        if required is not None:
            values = {"issue": checked_issue, "branch": checked_branch, "pr": checked_pr}
            _check(values[required] is not None, f"checkin kind {kind!r} requires {required}")
        _check(max_spool_events >= 1 and max_spool_bytes >= 512, "spool limits must be positive")
        _check(max_attempts >= 1 and base_backoff >= 0.0, "retry bounds must be positive")

        body_payload: dict[str, Any] = {}
        if payload is not None:
            _check(isinstance(payload, dict), "payload must be a JSON object")
            body_payload.update(payload)
        checked_note = _opt_text(note, "note", 2000)
        if checked_note is not None:
            body_payload["note"] = checked_note
        try:
            encoded_payload = json.dumps(body_payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise FleetArgumentError("payload must be JSON serializable") from exc
        _check(len(encoded_payload.encode("utf-8")) <= MAX_PAYLOAD_BYTES, "payload exceeds 16384 bytes")

        resolved_agent = self._agent(agent)
        raw_principal = (principal or os.environ.get("FLEET_PRINCIPAL") or "").strip()
        if not raw_principal and resolved_agent:
            raw_principal = resolved_agent if resolved_agent.startswith("agent-") else f"agent-{resolved_agent}"
        _check(bool(raw_principal), "principal or agent is required for checkin")
        checked_principal = _match(PATTERNS.ident, raw_principal, "principal")
        session_id = self._session(session, resolved_agent)
        checked_node = _match(
            PATTERNS.ident, (node or os.environ.get("FLEET_NODE") or short_host()).strip()[:128], "node"
        )

        spool_path = default_spool_path(spool_file)
        state = _load_spool(spool_path)
        sequences: dict[str, int] = state["sequences"]
        resolved_seq = _non_negative_int(seq, "seq") if seq is not None else int(sequences.get(session_id, 0)) + 1
        sequences[session_id] = max(int(sequences.get(session_id, 0)), resolved_seq)

        envelope: dict[str, Any] = {
            "schema_version": 1,
            "event_id": f"checkin:{session_id}:{resolved_seq}",
            "source": "checkin",
            "principal": checked_principal,
            "node": checked_node,
            "session_id": session_id,
            "seq": resolved_seq,
            "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - py3.10 stdlib clients
            "repo": checked_repo,
            "issue": checked_issue,
            "branch": checked_branch,
            "pr": checked_pr,
            "kind": kind,
            "redaction_version": 1,
            "payload": body_payload,
        }
        queue, dropped = _bound_queue([*state["queue"], envelope], max_spool_events, max_spool_bytes)
        state["queue"] = queue
        _save_spool(spool_path, state)

        delivered = 0
        degraded_reason: str | None = None
        while queue:
            batch = _next_batch(queue)
            sent = False
            for attempt in range(max_attempts):
                try:
                    self.request(
                        "POST", CHECKIN_PATH, body=batch, timeout=min(self.timeout, 5.0), retry_idempotent=False
                    )
                    sent = True
                    break
                except FleetAPIError as exc:
                    if exc.status not in _RETRYABLE_STATUSES:
                        raise
                    degraded_reason = f"http_{exc.status}" if exc.status else "hub_unreachable"
                    if attempt < max_attempts - 1 and base_backoff > 0:
                        sleep_fn(base_backoff * (2**attempt))
            if not sent:
                break
            delivered += len(batch)
            queue = queue[len(batch) :]
            state["queue"] = queue
            _save_spool(spool_path, state)

        return {
            "ok": True,
            "degraded": bool(queue),
            "spooled": bool(queue),
            "delivered": delivered,
            "pending": len(queue),
            "dropped": dropped,
            "seq": resolved_seq,
            "event_id": envelope["event_id"],
            **({"reason": degraded_reason} if queue and degraded_reason else {}),
        }


def checkin_commands(command_cls: Any) -> tuple[Any, ...]:
    """Build the CLI row for ``fleet_tools.COMMANDS`` (CLI-only; MCP uses connected-tier tools)."""
    return (
        command_cls(
            "checkin",
            "checkin",
            "Record an unconnected-tier activity check-in with bounded offline spool and backoff.",
            {
                "kind": {
                    "type": "string",
                    "description": "Kind: started, claimed, pushed, pr_opened, blocked, done, heartbeat.",
                },
                "repo": {"type": "string", "description": "Repository name, e.g. Runner_Dashboard."},
                "issue": {"type": "integer", "minimum": 1, "description": "Issue number (required for claimed)."},
                "branch": {"type": "string", "maxLength": 200, "description": "Branch name (required for pushed)."},
                "pr": {"type": "integer", "minimum": 1, "description": "PR number (required for pr_opened)."},
                "note": {"type": "string", "maxLength": 2000, "description": "Optional status or blocker note."},
                "payload": {"type": "object", "description": "Optional JSON metadata payload (<= 16 KiB)."},
                "node": {"type": "string", "maxLength": 128, "description": "Opaque node id ($FLEET_NODE)."},
                "principal": {"type": "string", "maxLength": 128, "description": "Principal id (agent-<agent>)."},
                "session": {"type": "string", "description": "Session id (default $FLEET_SESSION)."},
                "agent": {"type": "string", "description": "Agent name (default $FLEET_AGENT)."},
                "seq": {"type": "integer", "minimum": 0, "description": "Explicit idempotent sequence number."},
                "spool_file": {"type": "string", "description": "Offline spool path ($FLEET_CHECKIN_SPOOL)."},
            },
            required=("kind", "repo"),
            positional=("kind",),
        ),
    )
