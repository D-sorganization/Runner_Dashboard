#!/usr/bin/env python3
"""Recurring post-launch scan of merged commits and new issues/comments.

Issue #3086, epic #3077 slice 8; see docs/runbooks/public-repo-recurring-scan.md.
Reuses the publish guard's shared pattern list and the rd-barb alert channel:
a finding or a missed-run heartbeat raises one private alert, never public.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.publish_guard import load_rules  # noqa: E402
from scripts.rd_barb_notify import persona_notify  # noqa: E402
from scripts.scan_history import _repo_name  # noqa: E402

DEFAULT_MAX_GAP_SECONDS = 36 * 3600.0  # 1.5x a daily schedule


@dataclass(frozen=True)
class Finding:
    """A sanitized reference to a potential match: never the matched text itself."""

    source: str
    category: str


@dataclass(frozen=True)
class Cursor:
    last_commit: str | None = None
    last_item_at: str | None = None
    last_run_at: float | None = None


def load_cursor(path: Path) -> Cursor:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return Cursor(
        last_commit=data.get("last_commit"), last_item_at=data.get("last_item_at"), last_run_at=data.get("last_run_at")
    )


def save_cursor(path: Path, cursor: Cursor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    body = {"last_commit": cursor.last_commit, "last_item_at": cursor.last_item_at, "last_run_at": cursor.last_run_at}
    tmp.write_text(json.dumps(body), encoding="utf-8")
    tmp.replace(path)


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed: {args[0]}")
    return result.stdout


def new_commit_shas(repo: Path, since: str | None) -> list[str]:
    """New SHAs since ``since`` (exclusive), oldest first. ``None`` baselines at HEAD, no retroactive scan."""
    if since is None:
        return []
    out = _run(["git", "-C", str(repo), "log", f"{since}..HEAD", "--format=%H"])
    shas = [line for line in out.splitlines() if line.strip()]
    shas.reverse()
    return shas


def _match_categories(text: str, content: Mapping[str, Any]) -> list[str]:
    return [category for category, pattern in content.items() if pattern.search(text)]


def scan_commit_diff(repo: Path, sha: str, content: Mapping[str, Any]) -> list[Finding]:
    diff = _run(["git", "-C", str(repo), "show", sha, "--format=", "-U0"])
    added = (line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    return [Finding(f"commit:{sha[:12]}", c) for line in added for c in _match_categories(line, content)]


def filter_since(
    items: Iterable[Mapping[str, Any]], since_iso: str | None, timestamp_key: str = "created_at"
) -> list[Mapping[str, Any]]:
    """Keep only items timestamped strictly after ``since_iso`` (ISO-8601, lexically comparable)."""
    if since_iso is None:
        return list(items)
    return [item for item in items if str(item.get(timestamp_key, "")) > since_iso]


def scan_text_items(
    items: Iterable[Mapping[str, Any]], content: Mapping[str, Any], *, source_prefix: str
) -> list[Finding]:
    findings: list[Finding] = []
    for item in items:
        number = item.get("number", item.get("id", "?"))
        findings += [
            Finding(f"{source_prefix}:{number}", c) for c in _match_categories(str(item.get("body") or ""), content)
        ]
    return findings


def _fetch(endpoint: str, since_iso: str | None) -> list[dict[str, Any]]:
    if since_iso:
        endpoint += f"&since={since_iso}"
    return json.loads(_run(["gh", "api", "--paginate", endpoint]) or "[]")


def fetch_new_issues(repo_name: str, since_iso: str | None) -> list[dict[str, Any]]:
    return _fetch(f"repos/{repo_name}/issues?state=all&per_page=100", since_iso)


def fetch_new_comments(repo_name: str, since_iso: str | None) -> list[dict[str, Any]]:
    return _fetch(f"repos/{repo_name}/issues/comments?per_page=100", since_iso)


def heartbeat_status(last_run_at: float | None, now: float, max_gap_seconds: float) -> str:
    """``"missed"`` only once a prior run exists and the gap since then exceeds the tolerance."""
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    if last_run_at is None:
        return "ok"
    return "missed" if (now - last_run_at) > max_gap_seconds else "ok"


def build_finding_alert(findings: Sequence[Finding]) -> dict[str, Any]:
    categories = sorted({f.category for f in findings})
    locations = sorted({f.source for f in findings})
    return {
        "kind": "alert",
        "summary": f"Recurring scan: {len(findings)} potential finding(s) ({', '.join(categories)})",
        "body": "\n".join(f"- {loc}" for loc in locations),
        "urgency": "high",
        "dedupe_key": "scan-finding-" + "-".join(categories),
        "recommended_action": (
            "Review the named commit/issue/comment locations privately; never paste matched values into GitHub."
        ),
    }


def build_heartbeat_alert(max_gap_seconds: float) -> dict[str, Any]:
    return {
        "kind": "alert",
        "summary": "Recurring scan heartbeat missed",
        "body": f"No successful recurring-scan run in over {max_gap_seconds / 3600:.0f}h.",
        "urgency": "high",
        "dedupe_key": "scan-heartbeat-missed",
        "recommended_action": "Check the recurring-scan workflow run history for a disabled schedule or a failing job.",
    }


def run_scan(
    repo: Path,
    repo_name: str,
    cursor: Cursor,
    *,
    now: float,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    fetch_issues: Callable[[str, str | None], list[dict[str, Any]]] = fetch_new_issues,
    fetch_comments: Callable[[str, str | None], list[dict[str, Any]]] = fetch_new_comments,
    notify: Callable[[dict[str, Any]], dict[str, Any]] = lambda msg: persona_notify("rd-barb", msg),
) -> tuple[Cursor, list[dict[str, Any]]]:
    """Scan everything new since ``cursor`` and raise at most two private alerts. Never posts publicly."""
    rules = load_rules()
    alerts: list[dict[str, Any]] = []

    if heartbeat_status(cursor.last_run_at, now, max_gap_seconds) == "missed":
        alerts.append(notify(build_heartbeat_alert(max_gap_seconds)))

    findings: list[Finding] = []
    for sha in new_commit_shas(repo, cursor.last_commit):
        findings += scan_commit_diff(repo, sha, rules.content)

    if cursor.last_item_at is not None:
        issues = filter_since(fetch_issues(repo_name, cursor.last_item_at), cursor.last_item_at)
        comments = filter_since(fetch_comments(repo_name, cursor.last_item_at), cursor.last_item_at)
        findings += scan_text_items(issues, rules.content, source_prefix="issue")
        findings += scan_text_items(comments, rules.content, source_prefix="comment")

    if findings:
        alerts.append(notify(build_finding_alert(findings)))

    head = _run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
    stamp = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Cursor(last_commit=head, last_item_at=stamp, last_run_at=now), alerts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository to scan")
    parser.add_argument("--cursor", type=Path, required=True, help="cursor state file")
    parser.add_argument("--max-gap-hours", type=float, default=DEFAULT_MAX_GAP_SECONDS / 3600)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    repo_name = _repo_name(repo)
    cursor = load_cursor(args.cursor)
    try:
        new_cursor, alerts = run_scan(
            repo, repo_name, cursor, now=time.time(), max_gap_seconds=args.max_gap_hours * 3600
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Recurring scan failed: {exc}")
        return 1
    save_cursor(args.cursor, new_cursor)
    delivered = sum(1 for alert in alerts if alert.get("ok"))
    print(f"Recurring scan complete: {len(alerts)} alert(s) raised, {delivered} delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
