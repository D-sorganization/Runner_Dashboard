"""5 PM fleet productivity data pull (RD#3641, epic #3295 Grok Bot offload): one
batched GraphQL snapshot of merged PRs and closed issues per fleet repo over a
trailing window. Pure parsing/scoring plus the one subprocess fetch, shared by
the CLI and the API router (AGENTS.md: "no loops over repositories with gh")."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from staff.bob_forecast_score import REPO_ALIASES

PER_REPO_LIMIT = 50
WINDOW_HOURS = 24

FLEET_REPOS: tuple[str, ...] = tuple(REPO_ALIASES.values())

_Runner = Callable[..., subprocess.CompletedProcess[str]]


class RepoProductivity(TypedDict):
    repo: str
    merged_prs: int
    closed_issues: int
    merged_pr_numbers: list[int]


class ProductivitySnapshot(TypedDict):
    generated_at: str
    window_start: str
    window_end: str
    by_repo: list[RepoProductivity]
    total_merged_prs: int
    total_closed_issues: int


def default_window(now: datetime) -> tuple[datetime, datetime]:
    """The trailing ``WINDOW_HOURS`` window ending at ``now`` (one pull a day)."""
    return now - timedelta(hours=WINDOW_HOURS), now


def build_productivity_query(
    repos: Sequence[str], *, since: datetime, until: datetime, first: int = PER_REPO_LIMIT
) -> str:
    if not repos:
        raise ValueError("repos must not be empty")
    window = f"{since.isoformat()}..{until.isoformat()}"
    pr_fields = "nodes { ... on PullRequest { number } }"
    issue_fields = "nodes { ... on Issue { number } }"
    aliases = []
    for index, repo in enumerate(repos):
        pr_query = f"repo:{repo} is:pr is:merged merged:{window}"
        issue_query = f"repo:{repo} is:issue is:closed closed:{window}"
        aliases.append(f'p{index}: search(query: "{pr_query}", type: ISSUE, first: {first}) {{ {pr_fields} }}')
        aliases.append(f'i{index}: search(query: "{issue_query}", type: ISSUE, first: {first}) {{ {issue_fields} }}')
    return "query { " + " ".join(aliases) + " }"


def parse_productivity_response(payload: Mapping[str, object], repos: Sequence[str]) -> list[RepoProductivity]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("missing GraphQL data")
    results: list[RepoProductivity] = []
    for index, repo in enumerate(repos):
        pr_numbers = _search_numbers(data, f"p{index}", repo)
        issue_numbers = _search_numbers(data, f"i{index}", repo)
        results.append(
            {
                "repo": repo,
                "merged_prs": len(pr_numbers),
                "closed_issues": len(issue_numbers),
                "merged_pr_numbers": pr_numbers,
            }
        )
    return results


def _search_numbers(data: Mapping[str, object], key: str, repo: str) -> list[int]:
    search_result = data.get(key)
    nodes = search_result.get("nodes") if isinstance(search_result, Mapping) else None
    if not isinstance(nodes, list):
        raise ValueError(f"malformed search result {key!r} for {repo}")
    numbers: list[int] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError(f"malformed node in {key!r} for {repo}")
        number = node.get("number")
        if type(number) is not int or number <= 0:
            raise ValueError(f"invalid number in {key!r} for {repo}")
        numbers.append(number)
    return numbers


def build_snapshot(
    by_repo: Sequence[RepoProductivity], *, window_start: datetime, window_end: datetime, now: datetime
) -> ProductivitySnapshot:
    return {
        "generated_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "by_repo": list(by_repo),
        "total_merged_prs": sum(r["merged_prs"] for r in by_repo),
        "total_closed_issues": sum(r["closed_issues"] for r in by_repo),
    }


def fetch_productivity_payload(
    repos: Sequence[str], *, since: datetime, until: datetime, run: _Runner = subprocess.run
) -> dict[str, object]:
    query = build_productivity_query(repos, since=since, until=until)
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    response = run(cmd, capture_output=True, text=True, check=False, timeout=60)
    if response.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {response.stderr.strip()}")
    payload = json.loads(response.stdout)
    if not isinstance(payload, dict):
        raise ValueError("gh api graphql returned a non-object response")
    return payload


def pull_snapshot(
    repos: Sequence[str] = FLEET_REPOS, *, now: datetime | None = None, run: _Runner = subprocess.run
) -> ProductivitySnapshot:
    """Fetch and build the full productivity snapshot for ``repos`` over the
    trailing ``WINDOW_HOURS`` window ending at ``now`` (defaults to UTC-now)."""
    resolved_now = now if now is not None else datetime.now(UTC)
    window_start, window_end = default_window(resolved_now)
    payload = fetch_productivity_payload(repos, since=window_start, until=resolved_now, run=run)
    by_repo = parse_productivity_response(payload, repos)
    return build_snapshot(by_repo, window_start=window_start, window_end=window_end, now=resolved_now)
