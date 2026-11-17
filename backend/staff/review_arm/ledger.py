"""Durable exact-head verdicts and append-only operator overrides.

A negative head is sticky across subsequent reviews and process restarts.
Only a new SHA or an attributed operator record can remove the block; an
operator override does not itself supply an approval.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.review_arm.policy import Candidate


@dataclass(frozen=True)
class Verdict:
    repo: str
    pr: int
    head_sha: str
    verdict: str
    provider: str
    identity: str
    model: str
    blocking: int = 0
    observations: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    #: The reviewer's final message (bounded); box parity counts it as evidence.
    summary: str = ""

    def __post_init__(self) -> None:
        Candidate(self.repo, self.pr, self.head_sha)
        if self.verdict not in {"approve", "changes", "escalate"} or self.provider not in {"codex", "claude"}:
            raise ValueError("invalid review verdict/provider")
        if not self.identity.strip() or not self.model.strip() or type(self.blocking) is not int or self.blocking < 0:
            raise ValueError("reviewer identity/model and nonnegative blocking count required")
        if self.verdict == "approve" and self.blocking:
            raise ValueError("approve cannot contain BLOCKING findings")
        if any(not re.search(r"(?:[^\s]+:\d+|(?:CLAUDE|REVIEW)\.md.+)", entry) for entry in self.observations):
            raise ValueError("observations must cite a diff file:line or named repository rule")
        if any(not entry.strip() for entry in (*self.observations, *self.tests)):
            raise ValueError("evidence entries must not be blank")


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS review_arm_events (
                id INTEGER PRIMARY KEY, repo TEXT NOT NULL, pr INTEGER NOT NULL,
                head TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS review_arm_head ON review_arm_events(repo,pr,head,id)")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _append(self, repo: str, pr: int, head: str, kind: str, data: dict[str, object]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO review_arm_events(repo,pr,head,kind,data,created_at) VALUES(?,?,?,?,?,?)",
                (repo, pr, head, kind, json.dumps(data), datetime.now(UTC).isoformat()),
            )

    def ingest(self, verdict: Verdict) -> None:
        """Trusted ingestion boundary; caller binds evidence to the review run/diff.

        Changes without BLOCKING findings are approves under policy 2, but
        neither they nor explicit approves bypass the evidence bar: an approve
        with no observation, no test and a blank review body is a rubber stamp
        and escalates. (Box parity: armd armed any approve whose review text
        was non-empty, so a non-blank body is sufficient evidence.)
        """
        effective = verdict.verdict
        if verdict.verdict == "changes" and verdict.blocking == 0:
            effective = "approve"
        if effective == "approve" and not (verdict.observations or verdict.tests or verdict.summary.strip()):
            effective = "escalate"
        data = asdict(verdict)
        data["effective"] = effective
        self._append(verdict.repo, verdict.pr, verdict.head_sha, "verdict", data)

    def override(self, repo: str, pr: int, head: str, *, operator: str, reason: str) -> None:
        """Internal operator-only boundary; HTTP callers must authenticate upstream."""
        Candidate(repo, pr, head)
        if not operator.strip() or not reason.strip():
            raise ValueError("an override needs operator identity and reason")
        self._append(repo, pr, head, "override", {"operator": operator, "reason": reason})

    def state(self, repo: str, pr: int, head: str) -> tuple[bool, bool]:
        """(blocked, approved) at ``head``; imported box blocks/overrides match by prefix."""
        Candidate(repo, pr, head)
        blocked, approved = False, False
        for kind, row_head, data in self._rows(repo, pr):
            if kind == "override" and _head_match(row_head, head, prefix=True):
                blocked, approved = False, False
            elif kind == "blocked" and _head_match(row_head, head, prefix=True):
                blocked, approved = True, False
            elif kind != "verdict" or row_head != head:
                continue
            elif data["blocking"] > 0 and data["verdict"] in {"changes", "escalate"}:
                blocked, approved = True, False
            elif not blocked:
                approved = data["effective"] == "approve"
        return blocked, approved

    def is_blocked(self, repo: str, pr: int, head: str) -> bool:
        return self.state(repo, pr, head)[0]

    def _rows(self, repo: str, pr: int) -> list[tuple[str, str, dict[str, Any]]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT kind,head,data FROM review_arm_events WHERE repo=? AND pr=? ORDER BY id", (repo, pr)
            ).fetchall()
        return [(kind, head, json.loads(raw)) for kind, head, raw in rows]

    def _kind_rows(self, kind: str) -> list[tuple[str, int, str, dict[str, Any]]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT repo,pr,head,data FROM review_arm_events WHERE kind=? ORDER BY id", (kind,)
            ).fetchall()
        return [(repo, pr, head, json.loads(raw)) for repo, pr, head, raw in rows]

    # ── box state (arm_holds.txt / blocked_heads.txt / arm_override.txt) ──
    def import_box_state(self, *, holds: str, blocked: str, overrides: str) -> dict[str, int]:
        """One-time import of the box files, parsed exactly like intake.py/armd.py.

        Post: a second call is a no-op returning ``{"skipped": 1}``; afterwards the
        ledger is the only source (operators add holds/overrides via :meth:`hold`
        and :meth:`override_prefix`).
        """
        if self._kind_rows("box_import"):
            return {"skipped": 1}
        hold_rows = list(_parse_box_lines(holds, fields=2, strip_comment=True))
        blocked_rows = list(_parse_box_lines(blocked, fields=3, strip_comment=False))
        override_rows = list(_parse_box_lines(overrides, fields=3, strip_comment=True))
        for repo, pr, _rest in hold_rows:
            self._append(repo, pr, "*", "hold", {"source": "arm_holds.txt"})
        for repo, pr, rest in blocked_rows:
            self._append(repo, pr, rest[0], "blocked", {"source": "blocked_heads.txt", "why": " ".join(rest[1:])})
        for repo, pr, rest in override_rows:
            self._append(repo, pr, rest[0], "override", {"operator": "box", "reason": "arm_override.txt"})
        counts = {"holds": len(hold_rows), "blocked": len(blocked_rows), "overrides": len(override_rows)}
        self._append("*", 0, "*", "box_import", dict(counts))
        return counts

    def holds(self) -> frozenset[tuple[str, int]]:
        return frozenset((repo, pr) for repo, pr, _head, _data in self._kind_rows("hold"))

    def mark_blocked(self, repo: str, pr: int, head: str, *, why: str) -> None:
        """armd ``_mark_blocked``: sticky until a new head or an operator override."""
        Candidate(repo, pr, head)
        if not self.is_blocked(repo, pr, head):
            self._append(repo, pr, head, "blocked", {"why": why})

    # ── dispatch / verdict bookkeeping ──
    def record_dispatch(self, repo: str, pr: int, head: str, *, run_id: str, round_no: int, provider: str) -> None:
        Candidate(repo, pr, head)
        if not run_id.strip() or type(round_no) is not int or round_no < 1:
            raise ValueError("a dispatch needs a run id and a positive round")
        self._append(repo, pr, head, "dispatch", {"run_id": run_id, "round": round_no, "provider": provider})

    def rounds(self, repo: str, pr: int) -> int:
        return sum(1 for kind, _head, _data in self._rows(repo, pr) if kind == "dispatch")

    def dispatched_at(self, repo: str, pr: int, head: str) -> bool:
        return any(kind == "dispatch" and row_head == head for kind, row_head, _data in self._rows(repo, pr))

    def pending_dispatches(self) -> list[dict[str, Any]]:
        handled = {data["run_id"] for _r, _p, _h, data in self._kind_rows("handled")}
        return [
            {"repo": repo, "pr": pr, "head": head, **data}
            for repo, pr, head, data in self._kind_rows("dispatch")
            if data["run_id"] not in handled
        ]

    def attempts_at(self, repo: str, pr: int, head: str) -> dict[str, int]:
        """Our review runs at ``head``: still pending, finished with a verdict, failed."""
        dispatched = [
            d["run_id"] for kind, row_head, d in self._rows(repo, pr) if kind == "dispatch" and row_head == head
        ]
        outcomes = {d["run_id"]: str(d["outcome"]) for _r, _p, _h, d in self._kind_rows("handled")}
        failed = sum(1 for r in dispatched if outcomes.get(r, "").endswith("no_verdict"))
        done = sum(1 for r in dispatched if r in outcomes) - failed
        return {"pending": len(dispatched) - done - failed, "done": done, "failed": failed}

    def mark_handled(self, run_id: str, *, outcome: str) -> None:
        self._append("*", 0, "*", "handled", {"run_id": run_id, "outcome": outcome})

    def record_escalation(self, repo: str, pr: int, head: str, reason: str) -> bool:
        """True only the first time ``reason`` is escalated at this head (dedupe)."""
        for kind, row_head, data in self._rows(repo, pr):
            if kind == "escalation" and row_head == head and data.get("reason") == reason:
                return False
        self._append(repo, pr, head, "escalation", {"reason": reason})
        return True

    def escalations(self) -> list[dict[str, Any]]:
        return [{"repo": r, "pr": p, "head": h, **d} for r, p, h, d in self._kind_rows("escalation")]

    def record_arm(self, repo: str, pr: int, head: str, *, armed: bool, detail: str) -> None:
        self._append(repo, pr, head, "arm", {"armed": armed, "detail": detail[:300]})


def _head_match(recorded: str, head: str, *, prefix: bool) -> bool:
    """armd ``_is_blocked``: ``head.startswith(h) or h.startswith(head[:8])``."""
    if not prefix:
        return recorded == head
    return bool(recorded) and (head.startswith(recorded) or recorded.startswith(head[:8]))


def _parse_box_lines(text: str, *, fields: int, strip_comment: bool) -> Iterator[tuple[str, int, list[str]]]:
    for line in text.splitlines():
        parts = (line.split("#")[0] if strip_comment else line).split()
        if len(parts) < fields:
            continue
        try:
            number = int(parts[1])
            Candidate(parts[0], number, "0" * 40)
        except ValueError:
            continue
        if fields >= 3 and not re.fullmatch(r"[0-9a-f]{7,40}", parts[2]):
            continue
        yield parts[0], number, parts[2:]
