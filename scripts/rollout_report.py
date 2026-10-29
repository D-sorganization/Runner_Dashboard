#!/usr/bin/env python3
"""Playbook rollout reporting: one-line Barbara PR updates and a daily RM tally (#3490).

Posts a one-line summary comment to a configured PR (for example the "Barbara" exchange repo's
PR #69, docs/agents/barb-exchange.md) and a daily tally of playbook assignment states (#3486) to
RM #2376, both dry-run by default. Reuses the ``gh api`` argument-list posting pattern and
``<VAR>=on`` kill switch from ``scripts/barb_exchange_snapshot.py``, and the ``to:<who>`` label
convention from ``scripts/claude_barb_channel.py`` for the optional cross-repo escalation note.

  summary  --repo OWNER/NAME --number N --text "..." [--escalate-to-grok]
  tally    --repo OWNER/NAME --number N --assignments FILE|- [--escalate-to-grok]

Config (env): ROLLOUT_REPORT_LIVE (on|off, default off)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a file: make ``backend`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.staff.playbooks.schema import PlaybookAssignmentState

Poster = Callable[[str, int, str], dict[str, Any]]
Labeler = Callable[[str, int, str], dict[str, Any]]

ESCALATION_LABEL = "to:grok"
ESCALATION_MARKER = "<!-- rollout-report to:grok -->"


def rollout_report_live(env: Mapping[str, str]) -> bool:
    """Kill switch: only an explicit ``ROLLOUT_REPORT_LIVE=on`` posts anything."""
    return env.get("ROLLOUT_REPORT_LIVE", "off").strip().lower() == "on"


def format_one_line_summary(text: str, *, max_len: int = 500) -> str:
    """Collapse to a single line and cap length; never raises on odd input."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


def format_daily_tally(assignments: Sequence[Mapping[str, Any]], *, as_of: str) -> str:
    """Zero-filled count per :class:`PlaybookAssignmentState`, in enum declaration order."""
    counts: dict[PlaybookAssignmentState, int] = dict.fromkeys(PlaybookAssignmentState, 0)
    for assignment in assignments:
        try:
            state = PlaybookAssignmentState(str(assignment.get("state", "")).strip())
        except ValueError:
            continue
        counts[state] += 1
    parts = ", ".join(f"{state.value} {count}" for state, count in counts.items())
    return f"Playbook rollout tally {as_of}: {parts} (total {len(assignments)})"


def build_report_body(line: str, *, escalate_to_grok: bool) -> str:
    """The posted comment body: the formatted line, plus the ``to:grok`` marker when escalating."""
    return f"{line}\n\n{ESCALATION_MARKER}" if escalate_to_grok else line


def gh_post_comment(repo: str, number: int, body: str) -> dict[str, Any]:
    """Post one issue/PR comment with the ``gh`` CLI (argument list, no shell, no token handling)."""
    cmd = ["gh", "api", f"repos/{repo}/issues/{number}/comments", "--method", "POST", "-f", f"body={body}"]
    done = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)  # noqa: S603
    return json.loads(done.stdout)


def gh_add_label(repo: str, number: int, label: str) -> dict[str, Any]:
    """Add one label with the ``gh`` CLI (argument list, no shell)."""
    cmd = ["gh", "api", f"repos/{repo}/issues/{number}/labels", "--method", "POST", "-f", f"labels[]={label}"]
    done = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)  # noqa: S603
    return json.loads(done.stdout)


def post_report(
    repo: str,
    number: int,
    body: str,
    *,
    live: bool,
    escalate_to_grok: bool,
    poster: Poster = gh_post_comment,
    labeler: Labeler = gh_add_label,
) -> dict[str, Any]:
    """Post the report comment, then the ``to:grok`` label when escalating. Dry-run by default; never raises."""
    if not live:
        result: dict[str, Any] = {"ok": True, "status": "dry_run", "repo": repo, "number": number, "body": body}
        if escalate_to_grok:
            result["escalation_label"] = ESCALATION_LABEL
        return result
    try:
        comment = poster(repo, number, body)
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash the reporting job
        return {"ok": False, "status": "not_posted", "error": type(exc).__name__}
    result = {"ok": True, "status": "posted", "comment_url": comment.get("html_url")}
    if escalate_to_grok:
        try:
            labeler(repo, number, ESCALATION_LABEL)
            result["escalation"] = "labeled"
        except Exception as exc:  # noqa: BLE001 - label failure must not void the already-posted comment
            result["escalation"] = f"label_failed:{type(exc).__name__}"
    return result


def _read_assignments(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("assignments JSON must be a list")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    summary = sub.add_parser("summary", help="post a one-line summary comment")
    summary.add_argument("--repo", required=True)
    summary.add_argument("--number", type=int, required=True)
    summary.add_argument("--text", required=True)
    summary.add_argument("--escalate-to-grok", action="store_true")

    tally = sub.add_parser("tally", help="post the daily playbook assignment-state tally")
    tally.add_argument("--repo", required=True)
    tally.add_argument("--number", type=int, required=True)
    tally.add_argument("--assignments", default="-", help="assignments JSON file, or - for stdin")
    tally.add_argument("--as-of", default="", help="override the tally date label (default: today, UTC)")
    tally.add_argument("--escalate-to-grok", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = env if env is not None else os.environ
    args = _build_parser().parse_args(argv)
    live = rollout_report_live(env)
    if args.cmd == "summary":
        body = build_report_body(format_one_line_summary(args.text), escalate_to_grok=args.escalate_to_grok)
    else:
        try:
            assignments = _read_assignments(args.assignments)
        except (OSError, ValueError) as exc:
            print(f"::error::could not read assignments: {exc}", file=sys.stderr)
            return 1
        as_of = args.as_of or datetime.now(UTC).strftime("%Y-%m-%d")
        body = build_report_body(format_daily_tally(assignments, as_of=as_of), escalate_to_grok=args.escalate_to_grok)
    result = post_report(args.repo, args.number, body, live=live, escalate_to_grok=args.escalate_to_grok)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
