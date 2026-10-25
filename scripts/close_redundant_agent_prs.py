#!/usr/bin/env python3
"""Close redundant agent pull requests by fleet priority (#2895).

Uses ``GITHUB_TOKEN`` / ``GH_TOKEN`` only. Only PRs that *close* the same issue
(``Fixes|Closes|Resolves #N``) compete; ``Refs #N`` does not form a redundancy group.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

_CLOSING = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
_CLOSING_ISSUE_RE = re.compile(
    rf"\b{_CLOSING}\b[:\s]+(?:[\w.-]+/[\w.-]+)?#(\d+)\b",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FLEET_CAP = _REPO_ROOT / ".github" / "fleet-pr-cap"


def _load_fleet_module(rel_path: str, module_name: str) -> ModuleType:
    """Import a module from ``.github/fleet-pr-cap`` without shadowing conflicts."""
    cap_path = str(_FLEET_CAP)
    if cap_path not in sys.path:
        sys.path.insert(0, cap_path)
    target = _FLEET_CAP / rel_path
    spec = importlib.util.spec_from_file_location(module_name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load fleet module from {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_agent_identity = _load_fleet_module("shared_scripts/agent_identity.py", "fleet_agent_identity")
identify_agent = _agent_identity.identify_agent
outranks = _agent_identity.outranks
resolve_pr_agent = _agent_identity.resolve_pr_agent


def pr_closing_issue_numbers(title: str | None, body: str | None) -> frozenset[int]:
    text = f"{title or ''}\n{body or ''}"
    if not text.strip():
        return frozenset()
    return frozenset(int(m) for m in _CLOSING_ISSUE_RE.findall(text))

logger = logging.getLogger(__name__)

_DEFERRAL_TEMPLATE = (
    "Closing as redundant: another open pull request from a higher-priority fleet "
    "agent already closes #{issue}. If this lane still applies, reopen or coordinate "
    "on the winning PR."
)


@dataclass
class PullView:
    number: int
    title: str
    body: str
    agent: str | None
    raw: dict[str, Any]


@dataclass
class CloserStats:
    examined: int = 0
    closed: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def record_skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    def to_json(self) -> str:
        return json.dumps(
            {
                "examined": self.examined,
                "closed": self.closed,
                "skipped": self.skipped,
                "skip_reasons": self.skip_reasons,
            }
        )


def require_github_token() -> str:
    """Precondition: a GitHub token is available for API calls."""
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("::error::GH_TOKEN/GITHUB_TOKEN is empty; cannot run redundant-PR closer.", file=sys.stderr)
        raise SystemExit(1)
    return token


def _view_from_api(pr: dict[str, Any]) -> PullView:
    author = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    login = str(author.get("login") or "")
    is_bot = author.get("type") == "Bot" or login.endswith("[bot]")
    agent = resolve_pr_agent(pr)
    if agent is None and login:
        agent = identify_agent(login, is_bot)
    return PullView(
        number=int(pr["number"]),
        title=str(pr.get("title") or ""),
        body=str(pr.get("body") or ""),
        agent=agent,
        raw=pr,
    )


def _pick_winner(candidates: list[PullView]) -> PullView:
    """Return the highest-priority PR; lower PR number breaks ties."""
    assert candidates

    def sort_key(item: PullView) -> tuple[int, int]:
        agent = item.agent or "local"
        return (_agent_identity.priority_rank(agent), item.number)

    return min(candidates, key=sort_key)


def _agent_filter_set(raw: str | None) -> frozenset[str] | None:
    if not raw or not raw.strip():
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _eligible_to_close(view: PullView, agent_filter: frozenset[str] | None) -> bool:
    if view.agent in (None, "user"):
        return False
    if agent_filter is not None and view.agent not in agent_filter:
        return False
    return True


def run_closer(
    repo: str,
    *,
    pulls: list[dict[str, Any]],
    dry_run: bool,
    agent_filter: str | None,
    close_pr: Callable[[str, int], None],
    comment: Callable[[str, int, str], None],
) -> CloserStats:
    """Postcondition: returns counts for examined / closed / skipped PRs."""
    stats = CloserStats()
    filter_set = _agent_filter_set(agent_filter)
    views = [_view_from_api(pr) for pr in pulls]
    stats.examined = len(views)

    by_issue: dict[int, list[PullView]] = {}
    for view in views:
        for issue in pr_closing_issue_numbers(view.title, view.body):
            by_issue.setdefault(issue, []).append(view)

    for issue, group in sorted(by_issue.items()):
        if len(group) < 2:
            for view in group:
                stats.record_skip("sole_closer_on_issue")
            continue

        winner = _pick_winner(group)
        for view in group:
            if view.number == winner.number:
                continue
            if view.agent == "user":
                stats.record_skip("user_protected")
                continue
            if not _eligible_to_close(view, filter_set):
                stats.record_skip("not_eligible_agent")
                continue
            loser_agent = view.agent or "local"
            winner_agent = winner.agent or "local"
            if not outranks(winner_agent, loser_agent):
                stats.record_skip("not_lower_priority")
                continue
            if dry_run:
                stats.record_skip("dry_run")
                logger.info("Would close PR #%s (issue #%s)", view.number, issue)
                continue
            close_pr(repo, view.number)
            comment(repo, view.number, _DEFERRAL_TEMPLATE.format(issue=issue))
            stats.closed += 1

    return stats


def _write_summary(stats: CloserStats) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    reasons = ", ".join(f"{k}={v}" for k, v in sorted(stats.skip_reasons.items())) or "(none)"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            "## Agent Redundant PR Closer — results\n\n"
            f"- **PRs examined:** {stats.examined}\n"
            f"- **PRs closed:** {stats.closed}\n"
            f"- **PRs skipped:** {stats.skipped}\n"
            f"- **Skip reasons:** {reasons}\n"
        )


def _gh_json(token: str, args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", "api", "-H", f"Authorization: Bearer {token}", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "gh api failed"
        print(f"::error::{message}", file=sys.stderr)
        raise SystemExit(proc.returncode or 1)
    return json.loads(proc.stdout or "null")


def _default_close(token: str) -> Callable[[str, int], None]:
    def _close(repo: str, number: int) -> None:
        _gh_json(
            token,
            [
                "-X",
                "PATCH",
                f"repos/{repo}/pulls/{number}",
                "-f",
                "state=closed",
            ],
        )

    return _close


def _default_comment(token: str) -> Callable[[str, int, str], None]:
    def _comment(repo: str, number: int, body: str) -> None:
        _gh_json(
            token,
            [
                "-X",
                "POST",
                f"repos/{repo}/issues/{number}/comments",
                "-f",
                f"body={body}",
            ],
        )

    return _comment


def _fetch_open_pulls(token: str, repo: str) -> list[dict[str, Any]]:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    slug = repo if "/" in repo else f"{owner}/{repo}" if owner else repo
    data = _gh_json(token, [f"repos/{slug}/pulls?state=open&per_page=100"])
    if not isinstance(data, list):
        print("::error::Unexpected pulls payload from GitHub API.", file=sys.stderr)
        raise SystemExit(1)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Close redundant agent PRs by priority.")
    parser.add_argument("--repo", required=True, help="Repository name (owner/repo or short name)")
    parser.add_argument("--dry-run", action="store_true", help="Detect only; do not close")
    parser.add_argument("--agent-filter", default="", help="Comma-separated agent ids eligible to close")
    args = parser.parse_args(argv)

    token = require_github_token()
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    repo_slug = args.repo if "/" in args.repo else f"{owner}/{args.repo}" if owner else args.repo
    if not repo_slug or "/" not in repo_slug:
        print("::error::Could not resolve owner/repo for --repo.", file=sys.stderr)
        return 1

    pulls = _fetch_open_pulls(token, repo_slug)
    stats = run_closer(
        repo_slug,
        pulls=pulls,
        dry_run=bool(args.dry_run),
        agent_filter=args.agent_filter or None,
        close_pr=_default_close(token),
        comment=_default_comment(token),
    )
    _write_summary(stats)
    print(stats.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
