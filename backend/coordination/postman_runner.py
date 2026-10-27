"""Postman runner, dry run only (proposal ``docs/agents/messenger-agents.md``, slice 1).

Wires the pure postman engine (#2930) to the real board read path and a GitHub issue check, runs one
``cycle(dry_run=True)`` and keeps the last reports so an operator can see what it *would* deliver before
anything is switched on. Nothing here can write: the bus and the board-register adapter refuse every call,
and the delivery cursor is never saved. Live delivery is a later slice behind ``POSTMAN_LIVE``.
DbC: a report always carries ``dry_run: True``; a board that cannot be read yields a report with ``error``
instead of an exception, so a scheduler loop never dies on a GitHub outage.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coordination import board, roster
from coordination.postman import BOARD_OWNER_REPO, BoardRef, CycleReport, Delivery, Postman, PostmanState
from coordination.rm_scripts import ScriptResult, run_argv
from staff.workspace import rm_root

log = logging.getLogger("dashboard.coordination.postman_runner")

DEFAULT_BOARD_ISSUE = 2376  # RM config/agent_communication.json on 2026-10-27; read live when RM is present
FULL_AT = 2500  # GitHub's comment limit per issue (#1576 hit it)
REPORT_KEEP = 50
REPORT_FILE = "postman_reports.jsonl"
STATE_FILE = "postman_state.json"
DATA_DIR_ENV = "POSTMAN_DATA_DIR"
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GH_TIMEOUT = 30

Reader = Callable[[], dict[str, Any]]
InboxReader = Callable[[str, str], dict[str, Any]]
GhRunner = Callable[[list[str]], ScriptResult]


class DryRunOnlyError(RuntimeError):
    """A write was attempted while the postman may only plan."""


class BoardUnavailableError(RuntimeError):
    """The board or its issue could not be read; the cycle is reported as failed."""


class ReadOnlyBoard:
    """``BoardPort`` over the dashboard's cached RM board reads. ``register`` always refuses."""

    def __init__(self, sessions_reader: Reader = board.read_sessions, inbox_reader: InboxReader = board.read_inbox):
        self._sessions, self._inbox = sessions_reader, inbox_reader

    def read_sessions(self) -> list[dict[str, Any]]:
        data = self._sessions()
        if not data.get("available"):
            raise BoardUnavailableError(f"board unavailable: {data.get('reason') or 'no reason given'}")
        return list(data.get("sessions") or [])

    def read_inbox(self, session: str, repo: str) -> dict[str, Any]:
        return self._inbox(session, repo)

    def register(self, agent: str, session: str, issue: int) -> None:
        raise DryRunOnlyError(f"dry run: would register {session} for {agent} on #{issue}")


class RefusingBus:
    """``BusPort`` that delivers nothing: every call is a contract violation in a dry run."""

    def send(self, delivery: Delivery) -> str:
        raise DryRunOnlyError(f"dry run: would send {delivery.kind} to {delivery.to}")

    def ack(self, to: str, message_id: str) -> None:
        raise DryRunOnlyError(f"dry run: would ack {message_id} for {to}")


def _gh(argv: list[str]) -> ScriptResult:
    return run_argv(argv, cwd=Path.cwd(), timeout=GH_TIMEOUT)


def configured_board_issue() -> int | None:
    """The board issue RM currently writes to, or None when no RM checkout or config can be read."""
    root = rm_root()
    if root is None:
        return None
    try:
        data = json.loads((root / "config" / "agent_communication.json").read_text(encoding="utf-8"))
        return int(data["board_issue"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class GitHubBoardSource:
    """``BoardSource``: issue state via ``gh api``; the successor is RM's configured board when it differs."""

    def __init__(self, run: GhRunner = _gh, configured: Callable[[], int | None] = configured_board_issue) -> None:
        self._run, self._configured = run, configured

    def inspect(self, issue: int) -> BoardRef:
        res = self._run(["gh", "api", f"repos/{BOARD_OWNER_REPO}/issues/{issue}"])
        data = res.json() if res.rc == 0 else None
        if data is None:
            raise BoardUnavailableError(f"cannot read board issue #{issue}: {res.failure()}")
        configured = self._configured()
        successor = configured if configured is not None and configured != issue else None
        closed = data.get("state") == "closed"
        return BoardRef(issue, closed=closed, full=int(data.get("comments") or 0) >= FULL_AT, successor=successor)


def data_dir() -> Path:
    return Path(os.environ.get(DATA_DIR_ENV) or _DEFAULT_DATA_DIR)


def report_entry(report: CycleReport, at: datetime) -> dict[str, Any]:
    """One JSON-ready report. Post: ``dry_run`` is True."""
    if not report.dry_run:
        raise DryRunOnlyError("the runner only records dry-run cycles")
    actions = [asdict(a) for a in report.actions]
    counts = Counter(a.kind for a in report.actions)
    return {
        "at": at.isoformat(),
        "dry_run": True,
        "board_issue": report.board_issue,
        "counts": dict(counts),
        "actions": actions,
    }


def append_report(directory: Path, entry: dict[str, Any]) -> None:
    """Keep the newest ``REPORT_KEEP`` reports, one JSON per line, written atomically."""
    path = directory / REPORT_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [*lines, json.dumps(entry, sort_keys=True)][-REPORT_KEEP:]
    directory.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_reports(directory: Path | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Newest first; unreadable lines are skipped."""
    path = (directory or data_dir()) / REPORT_FILE
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def run_dry_cycle(
    *,
    board_port: ReadOnlyBoard | None = None,
    source: GitHubBoardSource | None = None,
    roster_ids: Sequence[str] | None = None,
    directory: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Plan one postman pass, record it and return the report. Never sends, registers or saves the cursor."""
    directory = directory or data_dir()
    state = PostmanState.load(
        directory / STATE_FILE, default_board_issue=configured_board_issue() or DEFAULT_BOARD_ISSUE
    )
    engine = Postman(
        board_port or ReadOnlyBoard(),
        RefusingBus(),
        source or GitHubBoardSource(),
        roster_ids or roster.agent_ids(),
        state,
        clock,
    )
    try:
        entry = report_entry(engine.cycle(dry_run=True), clock())
    except (BoardUnavailableError, subprocess.SubprocessError) as exc:
        entry = {"at": clock().isoformat(), "dry_run": True, "board_issue": state.board_issue, "error": str(exc)}
    append_report(directory, entry)
    log.info("postman dry run: board #%s %s", entry["board_issue"], entry.get("error") or entry.get("counts"))
    return entry
