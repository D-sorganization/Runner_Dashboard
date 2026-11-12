#!/usr/bin/env python3
"""Cursor cloud handoff check-in emitter (#3377, follow-up to #2874 / #3376).

Emits unconnected-tier ``pr_opened`` (requires ``pr``) and ``done`` (requires
``repo``) check-ins to ``POST /api/v1/activity/checkins`` using a credential
scoped to ``activity.checkin`` only. When the hub is unreachable or no check-in
token is configured, fails open without blocking the handoff.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FLEET_DIR = _REPO_ROOT / "clients" / "fleet"
if str(_FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(_FLEET_DIR))

from fleet_client import FleetAPIError, FleetArgumentError, FleetClient, default_session  # noqa: E402
from fleet_validators import PATTERNS, _check, _match, _opt, _opt_text, _positive_int  # noqa: E402

log = logging.getLogger("dashboard.scripts.cursor_cloud_handoff")

HANDOFF_CHECKIN_KINDS: tuple[str, ...] = ("pr_opened", "done")
CURSOR_AGENT = "cursor"
CURSOR_PRINCIPAL = "agent-cursor"
_ISSUE_SUFFIX_RE = re.compile(r"(?:^|[-_/])(\d{3,6})(?:$|[-_/])")


def _normalize_repo(repo: str) -> str:
    raw = (repo or "").strip().rstrip("/").removesuffix(".git")
    slug = raw.split("/")[-1] if "/" in raw else raw
    _check(bool(slug), "repo is required for handoff check-in")
    return _match(PATTERNS.repo, slug, "repo")


def _resolve_token(explicit: str | None = None) -> str | None:
    for candidate in (
        explicit,
        os.environ.get("FLEET_CHECKIN_TOKEN"),
        os.environ.get("FLEET_API_TOKEN"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _resolve_cursor_session(explicit: str | None = None) -> str:
    for candidate in (explicit, os.environ.get("FLEET_SESSION")):
        if candidate and candidate.strip().startswith(f"{CURSOR_AGENT}-"):
            return _match(PATTERNS.session, candidate.strip(), "session")
    return default_session(CURSOR_AGENT)


def emit_handoff_checkin(
    kind: str,
    repo: str,
    *,
    pr: int | None = None,
    issue: int | None = None,
    branch: str | None = None,
    note: str | None = None,
    base_url: str | None = None,
    token: str | None = None,
    session: str | None = None,
    node: str | None = None,
    spool_file: str | Path | None = None,
    client: FleetClient | None = None,
) -> dict[str, Any]:
    """Emit a ``pr_opened`` or ``done`` check-in; fail open on hub/transport errors."""
    _check(
        kind in HANDOFF_CHECKIN_KINDS,
        f"handoff checkin kind must be one of {', '.join(HANDOFF_CHECKIN_KINDS)}",
    )
    checked_repo = _normalize_repo(repo)
    checked_pr = _positive_int(pr, "pr") if (pr is not None or kind == "pr_opened") else None
    checked_issue = _positive_int(issue, "issue") if issue is not None else None
    checked_branch = _opt(PATTERNS.branch, branch, "branch")
    checked_note = _opt_text(note, 2000, "note")
    resolved_session = _resolve_cursor_session(session)

    resolved_token = _resolve_token(token)
    if client is None and not resolved_token:
        log.debug("cursor cloud handoff checkin skipped: no checkin token configured")
        return {
            "ok": True,
            "skipped": True,
            "degraded": False,
            "delivered": 0,
            "kind": kind,
            "repo": checked_repo,
            "reason": "no_checkin_token",
        }

    try:
        fc = client or FleetClient(
            base_url=base_url,
            token=resolved_token,
            agent=CURSOR_AGENT,
            session=resolved_session,
            timeout=5.0,
        )
        return fc.checkin(
            kind,
            checked_repo,
            pr=checked_pr,
            issue=checked_issue,
            branch=checked_branch,
            note=checked_note,
            node=node,
            principal=CURSOR_PRINCIPAL,
            session=resolved_session,
            agent=CURSOR_AGENT,
            spool_file=spool_file,
            max_attempts=1,
            base_backoff=0.0,
        )
    except FleetArgumentError:
        raise
    except (FleetAPIError, OSError, ValueError) as exc:
        status = getattr(exc, "status", 0)
        reason = f"http_{status}" if status else "hub_unreachable"
        log.debug("cursor cloud handoff checkin failed open (%s): %s", reason, exc)
        return {
            "ok": True,
            "skipped": False,
            "degraded": True,
            "delivered": 0,
            "kind": kind,
            "repo": checked_repo,
            "pr": checked_pr,
            "reason": reason,
        }


def _git_output(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _detect_pr_number(repo_root: Path, branch: str) -> int | None:
    for env_key in ("CURSOR_CLOUD_HANDOFF_TEST_PR_NUMBER", "FLEET_PR"):
        raw = os.environ.get(env_key, "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    if not branch:
        return None
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number", "-q", ".number"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return None
    out = proc.stdout.strip()
    return int(out) if proc.returncode == 0 and out.isdigit() and int(out) > 0 else None


def _detect_issue(branch: str) -> int | None:
    for env_key in ("CURSOR_CLOUD_HANDOFF_TEST_ISSUE", "FLEET_ISSUE"):
        raw = os.environ.get(env_key, "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    match = _ISSUE_SUFFIX_RE.search(branch or "")
    return int(match.group(1)) if match else None


def handoff_stop(
    repo_root: Path | None = None,
    *,
    client: FleetClient | None = None,
    spool_file: str | Path | None = None,
) -> dict[str, Any]:
    """Emit ``pr_opened`` (when a PR exists) and ``done`` check-ins on session stop."""
    root = (repo_root or Path.cwd()).resolve()
    repo = (
        os.environ.get("CURSOR_CLOUD_HANDOFF_TEST_REPO")
        or os.environ.get("FLEET_REPO")
        or _git_output(root, "config", "--get", "remote.origin.url")
        or root.name
    )
    branch = (
        os.environ.get("CURSOR_CLOUD_HANDOFF_TEST_BRANCH")
        or os.environ.get("FLEET_BRANCH")
        or _git_output(root, "rev-parse", "--abbrev-ref", "HEAD")
        or None
    )
    if branch == "HEAD":
        branch = None
    issue = _detect_issue(branch or "")
    pr_number = _detect_pr_number(root, branch or "")
    session = _resolve_cursor_session()

    events: list[dict[str, Any]] = []
    try:
        if pr_number is not None:
            events.append(
                emit_handoff_checkin(
                    "pr_opened",
                    repo,
                    pr=pr_number,
                    issue=issue,
                    branch=branch,
                    session=session,
                    spool_file=spool_file,
                    client=client,
                )
            )
        events.append(
            emit_handoff_checkin(
                "done",
                repo,
                pr=pr_number,
                issue=issue,
                branch=branch,
                session=session,
                spool_file=spool_file,
                client=client,
            )
        )
    except (FleetArgumentError, OSError, ValueError) as exc:
        log.debug("cursor cloud handoff stop skipped invalid context: %s", exc)
        return {"ok": True, "skipped": True, "events": events}
    return {"ok": True, "events": events}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stop", help="Cursor stop-hook entry: emit pr_opened/done check-ins and exit 0")
    chk = sub.add_parser("checkin", help="Emit an explicit pr_opened or done handoff check-in")
    chk.add_argument("kind", choices=HANDOFF_CHECKIN_KINDS)
    chk.add_argument("--repo", required=True)
    chk.add_argument("--pr", type=int, default=None)
    chk.add_argument("--issue", type=int, default=None)
    chk.add_argument("--branch", default=None)
    chk.add_argument("--note", default=None)
    chk.add_argument("--url", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv if argv is not None else sys.argv[1:])
    parser = _build_parser()
    try:
        ns = parser.parse_args(args_list)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if ns.command == "stop":
        if not sys.stdin.isatty():
            try:
                _ = sys.stdin.read()
            except OSError:
                pass
        handoff_stop()
        sys.stdout.write("{}\n")
        return 0

    try:
        result = emit_handoff_checkin(
            ns.kind,
            ns.repo,
            pr=ns.pr,
            issue=ns.issue,
            branch=ns.branch,
            note=ns.note,
            base_url=ns.url,
        )
    except FleetArgumentError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": "invalid_arguments", "message": str(exc)}) + "\n")
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
