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


def _create_successor_issue(repo: str, old_issue: int, runner: CommandRunner) -> int:
    title = f"Fleet Coordination Board (Rollover from RM #{old_issue})"
    body = (
        f"# Fleet Coordination Board (Rollover from RM #{old_issue})\n\n"
        f"**Created:** {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"**Predecessor:** #{old_issue}\n\n## Active Rules\n- Post concise STATUS / receipts.\n"
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

    journal: dict[str, Any] = {}
    if j_path.is_file():
        try:
            journal = json.loads(j_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            journal = {}

    stage, new_issue = str(journal.get("stage") or "INIT"), journal.get("new_issue")
    if stage in ("ISSUE_CREATED", "CONFIG_UPDATED") and new_issue and new_issue > 0:
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
        new_issue = _create_successor_issue(repo, old_issue, run)
        journal = {"stage": "ISSUE_CREATED", "old_issue": old_issue, "new_issue": new_issue, "predecessors": preds}
        atomic_write_json(j_path, journal)

    new_preds = ([p for p in preds if p != old_issue and p != new_issue] + [old_issue])[-MAX_PREDECESSORS:]
    raw_cfg["board_issue"], raw_cfg["board_predecessors"] = new_issue, new_preds
    atomic_write_json(cfg_path, raw_cfg)
    board_mod.reset_cache()

    journal["stage"], journal["predecessors"] = "CONFIG_UPDATED", new_preds
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
                f"<!-- agent-message v1 -->\nRollover: active board moved to #{new_issue}.",
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
