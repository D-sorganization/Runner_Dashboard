"""Fleet-wide open-PR line-cap scan (RD#3643): pure parsing/scoring, shared by the
CLI and API callers that each fetch one batched GraphQL response (AGENTS.md:
"no loops over repositories with gh")."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TypedDict

LINE_CAP = 500
PER_REPO_LIMIT = 50

_EXEMPT_LABELS = frozenset({"ship-alone"})
_DEPENDABOT_LOGIN = "dependabot[bot]"
_SNAPSHOT_BRANCH_PREFIXES = ("bot/sync-fleet-agents", "bot/collate-changes")


class ScannedPr(TypedDict):
    repo: str
    number: int
    title: str
    url: str
    additions: int
    changed_lines: int
    author: str
    is_draft: bool
    head_ref: str
    labels: list[str]


class ScanReport(TypedDict):
    generated_at: str
    cap: int
    scanned_repos: int
    scanned_prs: int
    exempt_prs: int
    violation_count: int
    violations: list[ScannedPr]


def build_graphql_query(repos: Sequence[str], *, per_repo_limit: int = PER_REPO_LIMIT) -> str:
    if not repos:
        raise ValueError("repos must not be empty")
    fields = (
        f"pullRequests(states: OPEN, first: {per_repo_limit}) {{ nodes {{ "
        "number title url additions deletions isDraft headRefName "
        "author { login } labels(first: 20) { nodes { name } } } }"
    )
    aliases = []
    for index, repo in enumerate(repos):
        parts = repo.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"invalid repo {repo!r}; expected 'owner/name'")
        aliases.append(f'r{index}: repository(owner: "{parts[0]}", name: "{parts[1]}") {{ {fields} }}')
    return "query { " + " ".join(aliases) + " }"


def parse_scan_response(payload: Mapping[str, object], repos: Sequence[str]) -> dict[str, list[ScannedPr]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("missing GraphQL data")
    result: dict[str, list[ScannedPr]] = {}
    for index, repo in enumerate(repos):
        repository = data.get(f"r{index}")
        if repository is None:
            result[repo] = []
            continue
        if not isinstance(repository, Mapping):
            raise ValueError(f"malformed response for {repo}")
        pull_requests = repository.get("pullRequests")
        nodes = pull_requests.get("nodes") if isinstance(pull_requests, Mapping) else None
        if not isinstance(nodes, list):
            raise ValueError(f"malformed pullRequests for {repo}")
        result[repo] = [_normalize_pr(repo, node) for node in nodes]
    return result


def _normalize_pr(repo: str, node: object) -> ScannedPr:
    if not isinstance(node, Mapping):
        raise ValueError(f"malformed PR node for {repo}")
    number, title, url = node.get("number"), node.get("title"), node.get("url")
    additions, deletions = node.get("additions"), node.get("deletions")
    is_draft, head_ref = node.get("isDraft"), node.get("headRefName")
    if type(number) is not int or number <= 0:
        raise ValueError(f"invalid PR number for {repo}")
    if not isinstance(title, str) or not isinstance(url, str) or not isinstance(head_ref, str):
        raise ValueError(f"invalid PR title/url/headRefName for {repo}#{number}")
    if type(additions) is not int or type(deletions) is not int or additions < 0 or deletions < 0:
        raise ValueError(f"invalid PR diff size for {repo}#{number}")
    if type(is_draft) is not bool:
        raise ValueError(f"invalid PR draft flag for {repo}#{number}")
    author = node.get("author")
    login = author.get("login") if isinstance(author, Mapping) else None
    return {
        "repo": repo,
        "number": number,
        "title": title,
        "url": url,
        "additions": additions,
        "changed_lines": additions + deletions,
        "author": login if isinstance(login, str) else "",
        "is_draft": is_draft,
        "head_ref": head_ref,
        "labels": _label_names(node.get("labels")),
    }


def _label_names(labels: object) -> list[str]:
    nodes = labels.get("nodes") if isinstance(labels, Mapping) else None
    if not isinstance(nodes, list):
        return []
    return [name for n in nodes if isinstance(n, Mapping) and isinstance(name := n.get("name"), str)]


def is_exempt(pr: Mapping[str, object]) -> bool:
    if pr.get("is_draft"):
        return True
    if pr.get("additions") == 0:
        return True
    if pr.get("author") == _DEPENDABOT_LOGIN:
        return True
    head_ref = pr.get("head_ref")
    if isinstance(head_ref, str) and head_ref.startswith(_SNAPSHOT_BRANCH_PREFIXES):
        return True
    labels = pr.get("labels")
    if isinstance(labels, list) and any(
        isinstance(label, str) and label.casefold() in _EXEMPT_LABELS for label in labels
    ):
        return True
    return False


def scan_violations(parsed: Mapping[str, Sequence[ScannedPr]], *, cap: int = LINE_CAP) -> list[ScannedPr]:
    violations = [pr for prs in parsed.values() for pr in prs if not is_exempt(pr) and pr["changed_lines"] > cap]
    violations.sort(key=lambda v: (v["repo"], v["number"]))
    return violations


def build_report(
    parsed: Mapping[str, Sequence[ScannedPr]],
    *,
    cap: int = LINE_CAP,
    now: datetime | None = None,
) -> ScanReport:
    all_prs = [pr for prs in parsed.values() for pr in prs]
    violations = scan_violations(parsed, cap=cap)
    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "cap": cap,
        "scanned_repos": len(parsed),
        "scanned_prs": len(all_prs),
        "exempt_prs": sum(1 for pr in all_prs if is_exempt(pr)),
        "violation_count": len(violations),
        "violations": violations,
    }
