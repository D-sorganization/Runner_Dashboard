"""Postman: deliver coordination-board messages into the agent bus (issue #2930).

It keeps presence alive for every roster agent, copies each board message into the recipient's bus inbox
exactly once, alerts Barb and the addressee on claims and name mentions, reports reader rejections instead
of dropping them, and follows the board to its successor issue. Pure engine over three ports; ``cycle(dry_run=True)``
plans and writes nothing. DbC: a ``Delivery`` must be well-formed (pre), is marked delivered only after the bus
accepted it (post), and ``ack`` requires a prior delivery.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("dashboard.coordination.postman")

BOARD_REPO = "Repository_Management"
BOARD_OWNER_REPO = f"D-sorganization/{BOARD_REPO}"
POSTMAN_SESSION = "postman-bus"
BARB = "barb"
RENEW_MARGIN = timedelta(minutes=30)
MAX_BODY = 4000
MAX_DELIVERED = 5000
CLAIM_RE = re.compile(r"\b(claim(?:s|ed|ing)?|check(?:ed|s|ing)?[- ]?out)\b", re.IGNORECASE)
REJECT_RE = re.compile(r"Rejected (identity change|unregistered sender)(?: for)? (\S+)")
REJECT_REASONS = {"identity change": "identity change", "unregistered sender": "unknown sender"}


class ContractError(ValueError):
    """A precondition or postcondition of the postman was violated."""


@dataclass(frozen=True)
class Delivery:
    """One message for the agent bus. Pre: recipient, body (<= 4000 chars) and an 8+ char key."""

    to: str
    kind: str  # message | alert | rejection
    type: str  # bus type: request | status
    body: str
    board_message_id: str
    idempotency_key: str
    repo: str = BOARD_REPO
    work_number: int = 0

    def __post_init__(self) -> None:
        if not self.to.strip():
            raise ContractError("delivery needs a recipient")
        if not self.body.strip() or len(self.body) > MAX_BODY:
            raise ContractError(f"delivery body must be 1..{MAX_BODY} characters")
        if len(self.idempotency_key) < 8:
            raise ContractError("idempotency key must be at least 8 characters")


@dataclass(frozen=True)
class BoardRef:
    """State of a board issue as reported by the board source."""

    issue: int
    closed: bool = False
    full: bool = False
    successor: int | None = None


@dataclass(frozen=True)
class Action:
    kind: str  # presence | message | alert | rejection | rollover | warning | error
    key: str
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CycleReport:
    dry_run: bool
    board_issue: int
    actions: tuple[Action, ...]


class BoardPort(Protocol):
    def read_sessions(self) -> list[dict[str, Any]]: ...
    def read_inbox(self, session: str, repo: str) -> dict[str, Any]: ...
    def register(self, agent: str, session: str, issue: int) -> None: ...


class BusPort(Protocol):
    def send(self, delivery: Delivery) -> str: ...
    def ack(self, to: str, message_id: str) -> None: ...


class BoardSource(Protocol):
    def inspect(self, issue: int) -> BoardRef: ...


def delivery_key(kind: str, to: str, message_id: str) -> str:
    """Stable bus idempotency key: the same board message to the same recipient never gets a new key."""
    return "pm-" + hashlib.sha256(f"{kind}|{to}|{message_id}".encode()).hexdigest()[:32]


@dataclass
class PostmanState:
    """The cursor: which deliveries were made, and which board issue is being followed."""

    board_issue: int
    delivered: list[str] = field(default_factory=list)

    def has(self, key: str) -> bool:
        return key in self.delivered

    def mark(self, key: str) -> None:
        if key not in self.delivered:
            self.delivered.append(key)
            del self.delivered[:-MAX_DELIVERED]

    def to_json(self) -> str:
        return json.dumps({"board_issue": self.board_issue, "delivered": self.delivered})

    @classmethod
    def from_json(cls, text: str) -> PostmanState:
        data = json.loads(text)
        return cls(board_issue=int(data["board_issue"]), delivered=[str(k) for k in data.get("delivered", [])])

    def save(self, path: Path) -> None:
        """Persist the cursor to ``path`` (JSON), atomically — a crash mid-write cannot corrupt it."""
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, *, default_board_issue: int) -> PostmanState:
        """Read the cursor from ``path``; a missing file starts fresh at ``default_board_issue``."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(board_issue=default_board_issue)
        return cls.from_json(text)


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _mentions(text: str, names: Sequence[str], *, bare: bool) -> set[str]:
    """Names mentioned as ``@name`` (or, for claim text, as a bare word)."""
    prefix = "" if bare else "@"
    found = {n for n in names if re.search(rf"(?<![\w@.-]){prefix}{re.escape(n)}(?![\w-])", text, re.IGNORECASE)}
    return {n.lower() for n in found}


class Postman:
    def __init__(
        self,
        board: BoardPort,
        bus: BusPort,
        source: BoardSource,
        roster: Sequence[str],
        state: PostmanState,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.board, self.bus, self.source = board, bus, source
        self.roster = tuple(roster)
        self.state = state
        self._clock = clock

    # -- planning (pure: reads only) -------------------------------------------------
    def _follow_rollover(self, actions: list[Action]) -> int:
        issue = self.state.board_issue
        ref = self.source.inspect(issue)
        if ref.issue == issue and (ref.closed or ref.full):
            if ref.successor is None or ref.successor == issue:
                actions.append(Action("warning", f"board:{issue}", f"board #{issue} is closed/full, no successor"))
            else:
                actions.append(Action("rollover", f"board:{issue}->{ref.successor}", f"following to #{ref.successor}"))
                issue = ref.successor
        return issue

    def _plan_presence(self, sessions: list[dict[str, Any]]) -> list[Action]:
        now, live = self._clock(), {str(s.get("session")): s for s in sessions}
        out = []
        for agent in self.roster:
            sid = f"pm-{agent}"
            current = live.get(sid)
            expires = _parse_time(current.get("expires")) if current else None
            if current is None or expires is None:
                out.append(Action("presence", f"presence:{sid}", "re-register", {"agent": agent, "session": sid}))
            elif expires - now <= RENEW_MARGIN:
                out.append(Action("presence", f"presence:{sid}", "renew", {"agent": agent, "session": sid}))
        return out

    def _read_inboxes(self, sessions: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], str]], list[str]]:
        msgs: dict[str, tuple[dict[str, Any], str]] = {}
        warnings: list[str] = []
        for s in sessions:
            sid, agent = str(s.get("session")), str(s.get("agent") or "")
            data = self.board.read_inbox(sid, str(s.get("repo") or BOARD_REPO))
            warnings += [str(w) for w in data.get("warnings") or []]
            for m in data.get("messages") or []:
                mid, target = str(m["id"]), (m["recipient"] if m["recipient"] != "*" else agent)
                msgs.setdefault(
                    f"{mid}|{target if target in self.roster else sid}", (m, target if target in self.roster else sid)
                )
        return list(msgs.values()), warnings

    def _alert_targets(self, m: dict[str, Any]) -> set[str]:
        text, names = str(m.get("text", "")), (*self.roster, BARB)
        targets = _mentions(text, names, bare=False)
        if m.get("recipient") in self.roster:
            targets.add(str(m["recipient"]))
        if CLAIM_RE.search(text):
            targets |= {BARB} | _mentions(text, self.roster, bare=True)
        if m.get("recipient") == BARB:
            targets.add(BARB)
        return targets - {str(m.get("sender_agent", ""))}

    def _plan_messages(self, inboxes: list[tuple[dict[str, Any], str]], issue: int) -> list[tuple[Action, Delivery]]:
        out: list[tuple[Action, Delivery]] = []
        alerted: set[str] = set()
        for m, to in inboxes:
            mid, text = str(m["id"]), str(m.get("text", ""))
            head = f"[board #{issue}] {m.get('session')} -> {m.get('recipient')}: "
            out.append(self._pair("message", "request", to, mid, head + text, issue))
            claim = bool(CLAIM_RE.search(text))
            for target in sorted(self._alert_targets(m)):
                if f"{mid}|{target}" in alerted:
                    continue
                alerted.add(f"{mid}|{target}")
                label = "claim/checkout" if claim else "addressed to you"
                out.append(self._pair("alert", "status", target, mid, f"[postman alert: {label}] {head}{text}", issue))
        return out

    def _plan_rejections(self, warnings: list[str], issue: int) -> list[tuple[Action, Delivery]]:
        out: list[tuple[Action, Delivery]] = []
        link = f"https://github.com/{BOARD_OWNER_REPO}/issues/{issue}"
        for w in dict.fromkeys(warnings):
            hit = REJECT_RE.search(w)
            sender, reason = (hit.group(2), REJECT_REASONS[hit.group(1)]) if hit else ("unknown", w[:120])
            body = f"[postman: board reader rejected a message] sender={sender} reason={reason} board={link}"
            action, delivery = self._pair("rejection", "status", BARB, f"{reason}:{sender}", body, issue)
            out.append((replace(action, payload={"sender": sender, "reason": reason, "link": link}), delivery))
        return out

    @staticmethod
    def _pair(kind: str, typ: str, to: str, mid: str, body: str, issue: int) -> tuple[Action, Delivery]:
        key = delivery_key(kind, to, mid)
        delivery = Delivery(to, kind, typ, body[:MAX_BODY], mid, key, work_number=issue)
        return Action(kind, key, f"to {to}", {"to": to, "board_message_id": mid}), delivery

    # -- one cycle ---------------------------------------------------------------------
    def cycle(self, dry_run: bool = False) -> CycleReport:
        """Plan and (unless ``dry_run``) apply one pass. Post: only accepted sends are marked delivered."""
        actions: list[Action] = []
        issue = self._follow_rollover(actions)
        sessions = self.board.read_sessions()
        presence = self._plan_presence(sessions)
        inboxes, warnings = self._read_inboxes(sessions)
        planned = self._plan_messages(inboxes, issue) + self._plan_rejections(warnings, issue)
        todo = [(a, d) for a, d in planned if not self.state.has(d.idempotency_key)]
        actions += presence + [a for a, _ in todo]
        if dry_run:
            return CycleReport(True, issue, tuple(actions))
        self.state.board_issue = issue
        for a in presence:
            self._apply(actions, a, partial(self.board.register, a.payload["agent"], a.payload["session"], issue))
        for a, d in todo:
            self._apply(actions, a, partial(self._deliver, d))
        return CycleReport(False, issue, tuple(actions))

    def _deliver(self, delivery: Delivery) -> None:
        self.bus.send(delivery)
        self.state.mark(delivery.idempotency_key)

    @staticmethod
    def _apply(actions: list[Action], action: Action, run: Callable[[], None]) -> None:
        try:
            run()
        except Exception as exc:  # noqa: BLE001 — one failed write must not stop the rest
            log.warning("postman: %s failed: %s", action.key, exc)
            actions.append(Action("error", action.key, str(exc)))

    def ack(self, to: str, message_id: str) -> None:
        """Mark a delivered board message read. Pre: it was delivered to ``to``."""
        if not self.state.has(delivery_key("message", to, message_id)):
            raise ContractError(f"message {message_id!r} was never delivered to {to!r}")
        self.bus.ack(to, message_id)
