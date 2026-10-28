"""Real ports for the postman engine (issue #2940, follow-up to #2930/#2941).

``RmBoard`` is a ``BoardPort`` over ``coordination.board``; ``AgentBus`` is a ``BusPort`` over
``agent_bus.store.AgentBusStore``; ``HubThreadBus`` (in ``coordination.hub_thread_bus``) is the
canonical ``BusPort`` over hub threads for the relay (#3235); ``ConfigBoardSource`` is a ``BoardSource`` that reads RM's
``config/agent_communication.json`` ``board_issue``. Each one is a thin translation layer (LoD):
no message storage or idempotency logic is duplicated here, it all lives in ``coordination.postman``,
``coordination.board`` and ``agent_bus.store``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, cast

from agent_bus.models import SendBody
from agent_bus.store import AgentBusStore
from coordination import board
from coordination.postman import BoardRef, Delivery
from identity import Principal
from staff.workspace import rm_root

log = logging.getLogger("dashboard.coordination.postman_adapters")

POSTMAN_SESSION = "postman-bus"
POSTMAN_ROLE = "postman"
#: Presence registration wants a branch; postman is not on one, so it reports its own session as a placeholder.
POSTMAN_BRANCH = "postman-bus"
_SendType = Literal["request", "status", "handoff", "blocked"]
_SEND_TYPES: frozenset[str] = frozenset({"request", "status", "handoff", "blocked"})
WORK_KIND: Literal["issue", "pr"] = "issue"
CONFIG_REL_PATH = Path("config") / "agent_communication.json"


class ConfigError(ValueError):
    """``config/agent_communication.json`` is missing or malformed. Fails closed: never a silent default."""


class RmBoard:
    """``BoardPort`` over ``coordination.board`` (the RM coordination board, issue #1229)."""

    def __init__(self, repo: str = board.BOARD_REPO) -> None:
        self.repo = repo

    def read_sessions(self) -> list[dict[str, Any]]:
        data = board.read_sessions(self.repo)
        if not data["available"]:
            raise RuntimeError(f"board unavailable: {data.get('reason')}")
        return data["sessions"]

    def read_inbox(self, session: str, repo: str) -> dict[str, Any]:
        data = board.read_inbox(session, repo)
        if not data.get("available", True):
            raise RuntimeError(f"board inbox unavailable for {session!r}: {data.get('reason')}")
        return data

    def register(self, agent: str, session: str, issue: int) -> None:
        if issue <= 0:
            raise ValueError("issue must be positive")
        board.publish(
            self.repo, session, "register", "--agent", agent, "--issue", str(issue), "--branch", POSTMAN_BRANCH
        )


class AgentBus:
    """``BusPort`` over ``agent_bus.store.AgentBusStore``.

    Registers a ``postman`` principal on construction (idempotent: re-registering only refreshes the
    lease), sends with the engine's idempotency key so a replayed ``send`` is a no-op on the store's
    side too, and acks. ``ack`` needs the bus's own message id, not the board message id the engine
    tracks, so a small in-memory map from ``(to, board_message_id)`` to the bus id is kept here — it is
    only a translation cache (not a parallel delivery store) and is rebuilt every run from ``send``.
    """

    def __init__(
        self, store: AgentBusStore, *, work_kind: Literal["issue", "pr"] = WORK_KIND, lease_seconds: int = 3600
    ) -> None:
        self.store = store
        self.work_kind = work_kind
        self._sent: dict[tuple[str, str], str] = {}
        principal = Principal(id=f"agent-{POSTMAN_ROLE}", type="bot", name=POSTMAN_ROLE, roles=["bot"])
        store.register(
            session_id=POSTMAN_SESSION,
            role=POSTMAN_ROLE,
            provider="postman",
            node="postman",
            principal=principal,
            lease_seconds=lease_seconds,
            mint_token=False,
        )

    def send(self, delivery: Delivery) -> str:
        if delivery.work_number <= 0:
            raise ValueError("delivery needs a positive work_number to reach the agent bus")
        if delivery.type not in _SEND_TYPES:
            raise ValueError(f"delivery type {delivery.type!r} is not a bus message type")
        body = SendBody(
            session=POSTMAN_SESSION,
            to=delivery.to,
            type=cast(_SendType, delivery.type),
            repo=delivery.repo,
            work_kind=self.work_kind,
            work_number=delivery.work_number,
            body=delivery.body,
            idempotency_key=delivery.idempotency_key,
        )
        result = self.store.send(body)
        message_id = str(result["message"]["id"])
        self._sent[(delivery.to, delivery.board_message_id)] = message_id
        return message_id

    def ack(self, to: str, message_id: str) -> None:
        bus_message_id = self._sent.get((to, message_id), message_id)
        try:
            self.store.ack(to, bus_message_id)
        except KeyError as exc:
            raise RuntimeError(f"cannot ack: {to!r} is not a registered agent-bus session") from exc
        except LookupError as exc:
            raise RuntimeError(f"cannot ack: message {bus_message_id!r} not found for {to!r}") from exc


class ConfigBoardSource:
    """``BoardSource`` reading RM's ``config/agent_communication.json`` ``board_issue``.

    Pre: the file exists under the RM checkout and ``board_issue`` is a positive int — a missing file
    or bad value raises ``ConfigError`` rather than defaulting silently. A followed issue that no
    longer matches the configured one is reported closed, with the configured issue as its successor,
    so ``Postman.cycle`` rolls over to it.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def _config_path(self) -> Path:
        root = self._root if self._root is not None else rm_root()
        if root is None:
            raise ConfigError("Repository_Management checkout not found (set STAFF_RM_ROOT)")
        return root / CONFIG_REL_PATH

    def board_issue(self) -> int:
        path = self._config_path()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
        issue = data.get("board_issue") if isinstance(data, dict) else None
        if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
            raise ConfigError(f"{path}: `board_issue` must be a positive integer")
        return issue

    def inspect(self, issue: int) -> BoardRef:
        configured = self.board_issue()
        if configured == issue:
            return BoardRef(issue=issue)
        return BoardRef(issue=issue, closed=True, successor=configured)
