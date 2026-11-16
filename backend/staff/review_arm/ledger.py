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
        neither they nor explicit approves bypass the evidence bar.
        """
        effective = verdict.verdict
        if verdict.verdict == "changes" and verdict.blocking == 0:
            effective = "approve"
        if effective == "approve" and (not verdict.observations or not verdict.tests):
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
        Candidate(repo, pr, head)
        blocked, approved = False, False
        with self._db() as db:
            rows = db.execute(
                "SELECT kind,data FROM review_arm_events WHERE repo=? AND pr=? AND head=? ORDER BY id", (repo, pr, head)
            ).fetchall()
        for kind, raw in rows:
            data = json.loads(raw)
            if kind == "override":
                blocked, approved = False, False
            elif data["blocking"] > 0 and data["verdict"] in {"changes", "escalate"}:
                blocked, approved = True, False
            elif not blocked:
                approved = data["effective"] == "approve"
        return blocked, approved
