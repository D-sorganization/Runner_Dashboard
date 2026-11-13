"""rd-barb decision ledger and confirm-only learned permissions (RD #3066, epic #3064).

Append-only JSONL ledger of Dieter's approve/deny decisions. Learning only *proposes* exact-match
``(action, repo, scope)`` rules as ``question`` envelopes; a rule is active only after Dieter's confirm
record. Anything touching a locked hold or safety limit is never learnable and always ``never``.
:func:`propose` and :func:`classify` are pure: records, policy and time are injected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from staff.rd_barb_memory import default_guidelines_path
from staff.rd_barb_policy import HoldLint, LockedPolicy, load_locked_policy

log = logging.getLogger("dashboard.staff.rd_barb_ledger")

OWNER = "dieter"
MIN_CONSISTENT = 3
PROPOSAL_TTL = timedelta(days=14)
MAX_SURFACINGS = 2  # first surfacing plus exactly one re-surface
DECISIONS = {"approve": "auto-ok", "deny": "never"}
Verdict = Literal["auto-ok", "ask-with-recommendation", "never"]
Record = Mapping[str, Any]


class RuleNotConfirmed(PermissionError):
    """A rule activation without a valid owner confirm; fails closed."""


@dataclass(frozen=True)
class Ask:
    action: str
    repo: str
    scope: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (self.action, self.repo, self.scope)):
            raise ValueError("action, repo and scope must be non-empty strings")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.action, self.repo, self.scope)


def default_ledger_path() -> Path:
    return default_guidelines_path().with_name("decisions.jsonl")


def is_locked(key: Iterable[str], policy: LockedPolicy) -> bool:
    """True when any part of the key names a locked entry or reads as lifting a hold."""
    parts = tuple(key)
    return bool(policy.touches(*parts) or HoldLint(policy).violation(" ".join(parts)))


def _key(record: Record) -> tuple[str, str, str]:
    return (record["action"], record["repo"], record["scope"])


def _proposal_id(key: tuple[str, str, str], verdict: str) -> str:
    return "rdb-rule-" + hashlib.sha256(json.dumps([*key, verdict]).encode()).hexdigest()[:16]


def _of(records: Iterable[Record], kind: str) -> list[Record]:
    return [r for r in records if r.get("kind") == kind]


def active_rules(records: Iterable[Record], policy: LockedPolicy) -> dict[tuple[str, str, str], str]:
    """Rules backed by an owner confirm record; locked keys are never matchable."""
    records = tuple(records)
    confirmed = {r["proposal_id"] for r in _of(records, "confirm") if r.get("confirmed_by") == OWNER}
    return {
        _key(p): p["verdict"]
        for p in _of(records, "proposal")
        if p["proposal_id"] in confirmed and not is_locked(_key(p), policy)
    }


def classify(ask: Ask, records: Iterable[Record], policy: LockedPolicy) -> Verdict:
    """``never`` for locked asks, the confirmed exact-match rule if any, else ``ask-with-recommendation``."""
    if is_locked(ask.key, policy):
        return "never"
    return active_rules(records, policy).get(ask.key, "ask-with-recommendation")  # type: ignore[return-value]


def propose(records: Iterable[Record], policy: LockedPolicy, now: datetime, n: int = MIN_CONSISTENT) -> list[dict]:
    """Question envelopes for keys with >= ``n`` consistent decisions, no contrary one, and no live proposal."""
    records = tuple(records)
    active, by_key = active_rules(records, policy), defaultdict(list)
    for record in _of(records, "decision"):
        by_key[_key(record)].append(record["decision"])
    out = []
    for key, decisions in by_key.items():
        if len(set(decisions)) != 1 or len(decisions) < n or key in active or is_locked(key, policy):
            continue
        verdict = DECISIONS[decisions[0]]
        pid = _proposal_id(key, verdict)
        prior = [p for p in _of(records, "proposal") if p["proposal_id"] == pid]
        last = max(prior, key=lambda p: p["surfacing"], default=None)
        if last and (last["surfacing"] >= MAX_SURFACINGS or now < datetime.fromisoformat(last["ts"]) + PROPOSAL_TTL):
            continue
        out.append(_envelope(key, verdict, pid, (last["surfacing"] if last else 0) + 1, now))
    return out


def _envelope(key: tuple[str, str, str], verdict: str, pid: str, surfacing: int, now: datetime) -> dict:
    action, repo, scope = key
    return {
        "id": f"{pid}-{surfacing}",
        "kind": "question",
        "text": f"You decided the same way on '{action}' in {repo} ({scope}) every time. Make it {verdict}?",
        "options": [
            {"id": "confirm", "label": f"Yes, treat this as {verdict}", "recommended": True},
            {"id": "keep-asking", "label": "No, keep asking me", "recommended": False},
        ],
        "recommended_option": "confirm",
        "correlation_id": pid,
        "urgency": "low",
        "expiry": (now + PROPOSAL_TTL).isoformat(),
        "surfacing": surfacing,
        "rule": {"action": action, "repo": repo, "scope": scope, "verdict": verdict},
    }


class DecisionLedger:
    """Append-only JSONL ledger. There is deliberately no update or delete API; reads return frozen records."""

    def __init__(
        self,
        path: Path | None = None,
        policy: LockedPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.path = path or default_ledger_path()
        self._policy = policy or load_locked_policy()
        self._clock, self._new_id, self._lock = clock, new_id, threading.Lock()

    def record_decision(self, ask: Ask, decision: str, *, source_thread_id: str, correlation_id: str) -> Record:
        if decision not in DECISIONS:
            raise ValueError("decision must be 'approve' or 'deny'")
        fields = {"action": ask.action, "repo": ask.repo, "scope": ask.scope, "decision": decision}
        return self._append(
            "decision", {**fields, "source_thread_id": source_thread_id, "correlation_id": correlation_id}
        )

    def record_proposal(self, envelope: Mapping[str, Any]) -> Record:
        rule = envelope["rule"]
        if is_locked((rule["action"], rule["repo"], rule["scope"]), self._policy):
            raise self._refuse("proposal touches a locked hold or safety limit")
        return self._append(
            "proposal", {**rule, "proposal_id": envelope["correlation_id"], "surfacing": envelope["surfacing"]}
        )

    def record_confirm(
        self, proposal_id: str, *, confirmed_by: str, source_thread_id: str, correlation_id: str
    ) -> Record:
        """Dieter's one-click confirm. Anyone else, or an unknown or locked proposal, fails closed."""
        if confirmed_by != OWNER:
            raise self._refuse(f"only {OWNER} can confirm a rule (got {confirmed_by!r})")
        if not any(p["proposal_id"] == proposal_id for p in _of(self.records(), "proposal")):
            raise self._refuse(f"no surfaced proposal {proposal_id!r}")
        return self._append(
            "confirm",
            {
                "proposal_id": proposal_id,
                "confirmed_by": confirmed_by,
                "source_thread_id": source_thread_id,
                "correlation_id": correlation_id,
            },
        )

    def records(self) -> tuple[Record, ...]:
        if not self.path.exists():
            return ()
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return tuple(MappingProxyType(json.loads(line)) for line in lines if line.strip())

    def _refuse(self, reason: str) -> RuleNotConfirmed:
        log.warning("rd-barb rule activation refused: %s", reason)
        return RuleNotConfirmed(reason)

    def _append(self, kind: str, fields: Mapping[str, Any]) -> Record:
        record = {"id": self._new_id(), "ts": self._clock().isoformat(), "kind": kind, **fields}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        return MappingProxyType(record)
