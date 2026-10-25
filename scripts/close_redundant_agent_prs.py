#!/usr/bin/env python3
"""Close redundant agent PRs by fleet priority (#2895); ``Refs #N`` is not a closer link."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

_CLOSING_ISSUE_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+(?:[\w.-]+/[\w.-]+)?#(\d+)\b",
    re.IGNORECASE,
)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FLEET_CAP = _REPO_ROOT / ".github" / "fleet-pr-cap"
_DEFERRAL = "Closing as redundant: a higher-priority open PR already closes #{issue}."


def _load_fleet_module(rel_path: str, module_name: str) -> ModuleType:
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


_ai = _load_fleet_module("shared_scripts/agent_identity.py", "fleet_agent_identity")
identify_agent = _ai.identify_agent
outranks = _ai.outranks
resolve_pr_agent = _ai.resolve_pr_agent


def pr_closing_issue_numbers(title: str | None, body: str | None) -> frozenset[int]:
    text = f"{title or ''}\n{body or ''}"
    return frozenset(int(m) for m in _CLOSING_ISSUE_RE.findall(text)) if text.strip() else frozenset()


@dataclass
class PullView:
    number: int
    title: str
    body: str
    agent: str | None


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
            {"examined": self.examined, "closed": self.closed, "skipped": self.skipped, "skip_reasons": self.skip_reasons}
        )


def require_github_token() -> str:
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("::error::GH_TOKEN/GITHUB_TOKEN is empty; cannot run redundant-PR closer.", file=sys.stderr)
        raise SystemExit(1)
    return token


def _view_from_api(pr: dict[str, Any]) -> PullView:
    author = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    login = str(author.get("login") or "")
    is_bot = author.get("type") == "Bot" or login.endswith("[bot]")
    agent = resolve_pr_agent(pr) or (identify_agent(login, is_bot) if login else None)
    return PullView(int(pr["number"]), str(pr.get("title") or ""), str(pr.get("body") or ""), agent)


def _pick_winner(candidates: list[PullView]) -> PullView:
    return min(candidates, key=lambda v: (_ai.priority_rank(v.agent or "local"), v.number))


def run_closer(
    repo: str,
    *,
    pulls: list[dict[str, Any]],
    dry_run: bool,
    agent_filter: str | None,
    close_pr: Callable[[str, int], None],
    comment: Callable[[str, int, str], None],
) -> CloserStats:
    stats = CloserStats(examined=len(pulls))
    filt = frozenset(p.strip() for p in (agent_filter or "").split(",") if p.strip()) or None
    by_issue: dict[int, list[PullView]] = {}
    for pr in pulls:
        view = _view_from_api(pr)
        for issue in pr_closing_issue_numbers(view.title, view.body):
            by_issue.setdefault(issue, []).append(view)

    for issue, group in sorted(by_issue.items()):
        if len(group) < 2:
            stats.record_skip("sole_closer_on_issue")
            continue
        winner = _pick_winner(group)
        for view in group:
            if view.number == winner.number or view.agent == "user":
                if view.number != winner.number:
                    stats.record_skip("user_protected")
                continue
            if filt is not None and view.agent not in filt:
                stats.record_skip("not_eligible_agent")
                continue
            if not outranks(winner.agent or "local", view.agent or "local"):
                stats.record_skip("not_lower_priority")
                continue
            if dry_run:
                stats.record_skip("dry_run")
                continue
            close_pr(repo, view.number)
            comment(repo, view.number, _DEFERRAL.format(issue=issue))
            stats.closed += 1
    return stats


def _write_summary(stats: CloserStats) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    reasons = ", ".join(f"{k}={v}" for k, v in sorted(stats.skip_reasons.items())) or "(none)"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            f"## Agent Redundant PR Closer — results\n\n"
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
        print(f"::error::{proc.stderr.strip() or proc.stdout.strip() or 'gh api failed'}", file=sys.stderr)
        raise SystemExit(proc.returncode or 1)
    return json.loads(proc.stdout or "null")


def _repo_slug(name: str) -> str:
    if "/" in name:
        return name
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    return f"{owner}/{name}" if owner else name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agent-filter", default="")
    args = parser.parse_args(argv)
    token = require_github_token()
    slug = _repo_slug(args.repo)
    if "/" not in slug:
        print("::error::Could not resolve owner/repo for --repo.", file=sys.stderr)
        return 1
    pulls = _gh_json(token, [f"repos/{slug}/pulls?state=open&per_page=100"])
    if not isinstance(pulls, list):
        print("::error::Unexpected pulls payload from GitHub API.", file=sys.stderr)
        return 1

    def close(repo: str, num: int) -> None:
        _gh_json(token, ["-X", "PATCH", f"repos/{repo}/pulls/{num}", "-f", "state=closed"])

    def post_comment(repo: str, num: int, body: str) -> None:
        _gh_json(token, ["-X", "POST", f"repos/{repo}/issues/{num}/comments", "-f", f"body={body}"])

    stats = run_closer(
        slug,
        pulls=pulls,
        dry_run=bool(args.dry_run),
        agent_filter=args.agent_filter or None,
        close_pr=close,
        comment=post_comment,
    )
    _write_summary(stats)
    print(stats.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
