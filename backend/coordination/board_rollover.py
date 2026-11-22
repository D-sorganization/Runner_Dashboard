"""Atomic, crash-recoverable coordination board rollover (issue #4574).

Automatically rolls over the active coordination board issue when it reaches
~500 comments, creating a successor issue, atomically moving the retired board
into `board_predecessors` in `config/agent_communication.json`, notifying the old
board, and resetting in-memory board caches.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from config_schema import atomic_write_json
from coordination import board as board_mod
from staff.workspace import rm_root

log = logging.getLogger("dashboard.coordination.board_rollover")
DEFAULT_ROLLOVER_THRESHOLD: int = 500
MAX_PREDECESSORS: int = 3
DEFAULT_BOARD_REPO: str = "D-sorganization/Repository_Management"
CommandRunner = Callable[[Sequence[str]], Any]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    exe = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class RolloverResult:
    rolled_over: bool
    old_issue: int
    new_issue: int | None
    predecessors: list[int]
    comment_count: int
    reason: str


def _read_rm_config(config_path: Path) -> tuple[str, int, list[int], dict[str, Any]]:
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Root of {config_path} must be a JSON dict")
    repo = str(raw.get("board_repo") or DEFAULT_BOARD_REPO)
    issue = int(raw.get("board_issue") or 0)
    if issue <= 0:
        raise ValueError(f"Invalid board_issue in {config_path}: {issue}")
    return repo, issue, [int(p) for p in raw.get("board_predecessors") or [] if int(p) > 0], raw


def _fetch_issue_comments(repo: str, issue: int, runner: CommandRunner) -> int:
    res = runner(["gh", "api", f"repos/{repo}/issues/{issue}"])
    if getattr(res, "returncode", None) != 0:
        raise RuntimeError(f"Failed to inspect issue {repo}#{issue}: {getattr(res, 'stderr', '')}")
    return int(json.loads(getattr(res, "stdout", "") or "{}").get("comments") or 0)


def _create_successor_issue(repo: str, old_issue: int, marker: str, runner: CommandRunner) -> int:
    title = f"Fleet Coordination Board (Rollover from RM #{old_issue})"
    body = (
        f"# Fleet Coordination Board (Rollover from RM #{old_issue})\n\n"
        f"**Created:** {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"**Predecessor:** #{old_issue}\n\n{marker}\n\n## Active Rules\n- Post concise STATUS / receipts.\n"
    )
    res = runner(["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body])
    if getattr(res, "returncode", None) != 0:
        raise RuntimeError(f"Failed to create successor issue for {repo}#{old_issue}: {getattr(res, 'stderr', '')}")
    out = getattr(res, "stdout", "").strip()
    match = re.search(r"/issues/(\d+)", out)
    if match:
        return int(match.group(1))
    if out.isdigit():
        return int(out)
    raise RuntimeError(f"Could not parse issue number from output: {out}")


def _recover_created_issue(repo: str, marker: str, runner: CommandRunner) -> int:
    """Reconcile an uncertain create without ever replaying the remote write."""
    res = runner(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/issues?state=all&per_page=100"])
    if getattr(res, "returncode", None) != 0:
        raise RuntimeError("Cannot reconcile successor creation; retain journal and retry")
    pages = json.loads(res.stdout)
    matches = [
        issue["number"]
        for page in pages
        for issue in page
        if "pull_request" not in issue and marker in (issue.get("body") or "")
    ]
    if len(matches) != 1 or type(matches[0]) is not int or matches[0] <= 0:
        raise RuntimeError("Successor creation ambiguous; retain journal for operator reconciliation")
    return matches[0]


def _validate_journal(journal: Any, repo: str, current_issue: int, current_preds: list[int]) -> None:
    """Reject corrupt/stale state before remote writes or config mutation."""
    if not isinstance(journal, dict) or journal.get("stage") not in ("CREATING", "ISSUE_CREATED", "CONFIG_UPDATED"):
        raise ValueError("Invalid rollover journal stage")
    old, new, preds = journal.get("old_issue"), journal.get("new_issue"), journal.get("predecessors")
    if type(old) is not int or old <= 0 or not isinstance(preds, list):
        raise ValueError("Invalid rollover journal original board")
    if any(type(p) is not int or p <= 0 for p in preds) or len(set(preds)) != len(preds):
        raise ValueError("Invalid rollover journal predecessors")
    if journal.get("repo", repo) != repo:
        raise ValueError("Rollover journal repository mismatch")
    if journal["stage"] == "CREATING":
        marker = journal.get("marker")
        if (
            new is not None
            or not isinstance(marker, str)
            or not re.fullmatch(r"<!-- board-rollover:[0-9a-f]{32} -->", marker)
        ):
            raise ValueError("Invalid rollover creation intent")
    elif type(new) is not int or new <= 0 or new == old or new in preds:
        raise ValueError("Invalid rollover journal successor")
    expected = ([p for p in preds if p != old] + [old])[-MAX_PREDECESSORS:]
    if current_issue == old and current_preds == preds:
        return
    if current_issue == new and current_preds == expected:
        return
    raise ValueError("Rollover journal conflicts with current configuration")


def _rollover_notice_envelope(repo: str, new_issue: int) -> str:
    """Format a valid ``agent-message v1`` envelope for the retired board."""
    repo_name = repo.rsplit("/", 1)[-1] or "Repository_Management"
    payload = {
        "version": 1,
        "id": str(uuid4()),
        "kind": "message",
        "session": "board-rollover",
        "repo": repo_name,
        "at": datetime.now(UTC).isoformat(),
        "recipient": "*",
        "text": f"Rollover: active board moved to #{new_issue}.",
    }
    return f"<!-- agent-message v1 -->\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"


def execute_board_rollover(
    rm_path: Path | None = None,
    *,
    threshold: int = DEFAULT_ROLLOVER_THRESHOLD,
    force: bool = False,
    runner: CommandRunner | None = None,
    journal_path: Path | None = None,
) -> RolloverResult:
    """Perform atomic, crash-recoverable board rollover when comment count >= threshold."""
    if threshold <= 0:
        raise ValueError("threshold must be a positive integer")
    run, root = runner or _default_runner, rm_path or rm_root()
    if root is None:
        raise RuntimeError("Repository_Management root directory cannot be resolved")

    cfg_path = root / "config" / "agent_communication.json"
    repo, old_issue, preds, raw_cfg = _read_rm_config(cfg_path)
    j_path = journal_path or (root / "config" / "rollover_journal.json")

    if j_path.is_file():
        journal = json.loads(j_path.read_text(encoding="utf-8"))
        _validate_journal(journal, repo, old_issue, preds)
        old_issue, preds = journal["old_issue"], journal["predecessors"]
        if journal["stage"] == "CREATING":
            journal["new_issue"] = _recover_created_issue(repo, journal["marker"], run)
            journal["stage"] = "ISSUE_CREATED"
            _validate_journal(journal, repo, old_issue, preds)
            atomic_write_json(j_path, journal)
        new_issue = journal["new_issue"]
        comment_count = threshold
    else:
        comment_count = _fetch_issue_comments(repo, old_issue, run)
        if not force and comment_count < threshold:
            return RolloverResult(
                False,
                old_issue,
                None,
                preds,
                comment_count,
                f"comment count {comment_count} below threshold {threshold}",
            )
        marker = f"<!-- board-rollover:{uuid4().hex} -->"
        journal = {
            "stage": "CREATING",
            "repo": repo,
            "old_issue": old_issue,
            "new_issue": None,
            "predecessors": preds,
            "marker": marker,
        }
        atomic_write_json(j_path, journal)
        new_issue = _create_successor_issue(repo, old_issue, marker, run)
        journal.update(stage="ISSUE_CREATED", new_issue=new_issue)
        _validate_journal(journal, repo, old_issue, preds)
        atomic_write_json(j_path, journal)

    new_preds = ([p for p in preds if p != old_issue and p != new_issue] + [old_issue])[-MAX_PREDECESSORS:]
    raw_cfg["board_issue"], raw_cfg["board_predecessors"] = new_issue, new_preds
    atomic_write_json(cfg_path, raw_cfg)
    board_mod.reset_cache()

    journal["stage"] = "CONFIG_UPDATED"
    atomic_write_json(j_path, journal)

    try:
        run(
            [
                "gh",
                "issue",
                "comment",
                str(old_issue),
                "--repo",
                repo,
                "--body",
                _rollover_notice_envelope(repo, new_issue),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Notice on old board #%s failed: %s", old_issue, exc)

    try:
        j_path.unlink(missing_ok=True)
    except OSError:
        pass
    return RolloverResult(
        True, old_issue, new_issue, new_preds, comment_count, f"rolled over from #{old_issue} to #{new_issue}"
    )
