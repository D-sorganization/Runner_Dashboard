"""Fleet merge shepherd pass: GraphQL snapshot → eligibility → capped auto-arm (#Bob merge-rate)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coordination.merge_shepherd import (
    PRCandidate,
    ShepherdAction,
    ShepherdDecision,
    get_auto_merge_flag,
    rank_candidates_for_shepherding,
)
from coordination.merge_shepherd_low_risk import arm_eligibility_reason
from coordination.merge_shepherd_low_risk import low_risk_line_cap as default_low_risk_line_cap

log = logging.getLogger(__name__)

ORG = "D-sorganization"
SHEPHERD_REPOS: tuple[str, ...] = (
    f"{ORG}/Runner_Dashboard",
    f"{ORG}/Repository_Management",
    f"{ORG}/Gasification_Model",
    f"{ORG}/UpstreamDrift",
    f"{ORG}/AffineDrift",
    f"{ORG}/Tools_Private",
)
RD_REPO = f"{ORG}/Runner_Dashboard"
QUEUE_BACKOFF_THRESHOLD = 6
ARM_CAP_RD = 2
ARM_CAP_DEFAULT = 4
SHEPHERD_HOLD_LABELS = frozenset({"hold", "do-not-merge", "needs-dieter", "dnm"})
_PR_REF = re.compile(r"\b(?:PR\s*#|#)(\d+)\b", re.IGNORECASE)

OPEN_PRS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    mergeQueue {
      entries(first: 100) { totalCount }
    }
    armedPullRequests: pullRequests(states: OPEN, first: 100) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        autoMergeRequest { enabledAt }
      }
    }
    pullRequests(states: OPEN, first: 100) {
      nodes {
        number
        title
        isDraft
        mergeable
        mergeStateStatus
        reviewDecision
        headRefOid
        additions
        deletions
        labels(first: 30) { nodes { name } }
        files(first: 100) {
          pageInfo { hasNextPage }
          nodes { path }
        }
        autoMergeRequest { enabledAt }
        mergeQueueEntry { position }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup { state }
            }
          }
        }
      }
    }
  }
}
"""

ARMED_PULL_REQUESTS_PAGE_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    armedPullRequests: pullRequests(states: OPEN, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        autoMergeRequest { enabledAt }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class SkipPr:
    repo: str
    number: int


@dataclass
class PassConfig:
    live: bool = False
    dry_run: bool = True
    repo_root: Path = field(default_factory=Path.cwd)
    skip_path: Path | None = None
    low_risk_line_cap: int = field(default_factory=default_low_risk_line_cap)


@dataclass(frozen=True)
class PassAction:
    repo: str
    pr_number: int
    head_sha: str
    strategy: str
    dry_run: bool
    detail: str
    eligibility: str = ""


@dataclass
class PassSummary:
    repos: list[dict[str, Any]] = field(default_factory=list)
    armed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    backoff: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def default_skip_path(repo_root: Path) -> Path:
    return repo_root / "config" / "merge_shepherd_skip.json"


def load_skip_list(path: Path) -> frozenset[tuple[str, int]]:
    if not path.is_file():
        return frozenset()
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("skip_prs") if isinstance(raw, dict) else raw
    out: set[tuple[str, int]] = set()
    for row in items or []:
        if not isinstance(row, Mapping):
            continue
        repo = str(row.get("repo") or "").strip()
        num = row.get("number")
        if repo and isinstance(num, int) and num > 0:
            out.add((repo, num))
    return frozenset(out)


def _repo_arm_cap(repo: str) -> int:
    return ARM_CAP_RD if repo == RD_REPO else ARM_CAP_DEFAULT


def _checks_state(node: Mapping[str, Any]) -> str:
    commits = (node.get("commits") or {}).get("nodes") or []
    if not commits:
        return "UNKNOWN"
    commit = commits[0].get("commit") if isinstance(commits[0], Mapping) else {}
    rollup = (commit or {}).get("statusCheckRollup") if isinstance(commit, Mapping) else {}
    return str((rollup or {}).get("state") or "UNKNOWN").upper()


def _label_names(node: Mapping[str, Any]) -> tuple[str, ...]:
    labels = (node.get("labels") or {}).get("nodes") or []
    return tuple(str(n.get("name") or "") for n in labels if isinstance(n, Mapping) and n.get("name"))


def label_blocks_shepherd(node: Mapping[str, Any]) -> str:
    for name in _label_names(node):
        normalized = name.strip().casefold()
        if normalized in SHEPHERD_HOLD_LABELS:
            return f"label_{normalized.replace('-', '_')}"
    return ""


HOLDS_UNAVAILABLE_PREFIX = "holds_unavailable"


def resolve_hub_holds_policy(
    *,
    mode: str,
    dashboard_url: str,
    token: str,
    fetch_holds: Callable[[str, str], list[dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Hub schedule holds are required in production; fail closed when unavailable."""
    normalized = mode.strip().lower()
    if normalized in {"", "off", "false", "0"}:
        return [], None
    url = dashboard_url.strip().rstrip("/")
    auth = token.strip()
    if not url or not auth:
        if normalized == "required":
            return [], f"{HOLDS_UNAVAILABLE_PREFIX}:not_configured"
        return [], None
    if fetch_holds is None:
        return [], f"{HOLDS_UNAVAILABLE_PREFIX}:fetcher_missing"
    try:
        holds = fetch_holds(url, auth)
    except OSError as exc:
        if normalized == "required":
            return [], f"{HOLDS_UNAVAILABLE_PREFIX}:unreachable:{exc}"
        return [], None
    return holds, None


def resolve_live_arming(
    *,
    merge_shepherd_live: str,
    input_dry_run: str,
    cli_dry_run: bool,
    github_event_name: str = "",
) -> bool:
    """Scheduled runs ignore workflow_dispatch dry_run defaults so MERGE_SHEPHERD_LIVE can arm."""
    if cli_dry_run:
        return False
    dry_input = input_dry_run
    if github_event_name and github_event_name != "workflow_dispatch":
        dry_input = ""
    if dry_input.strip().lower() in {"true", "1", "yes"}:
        return False
    return merge_shepherd_live.strip().lower() == "true"


def holds_gate_failed_payload(*, live: bool, reason: str) -> dict[str, Any]:
    summary = PassSummary(errors=[reason])
    payload = summary_to_json(summary)
    payload["live"] = live
    payload["armed"] = []
    payload["executed"] = []
    payload["holds_unavailable"] = reason
    payload["status"] = "holds_unavailable"
    return payload


def _pr_file_metadata(node: Mapping[str, Any]) -> tuple[tuple[str, ...], int, int, bool]:
    files_block = node.get("files") if isinstance(node.get("files"), Mapping) else {}
    file_nodes = (files_block.get("nodes") or []) if isinstance(files_block, Mapping) else []
    paths = tuple(str(n.get("path") or "") for n in file_nodes if isinstance(n, Mapping) and n.get("path"))
    page_info = files_block.get("pageInfo") if isinstance(files_block, Mapping) else {}
    has_next = bool((page_info or {}).get("hasNextPage")) if isinstance(page_info, Mapping) else False
    additions = int(node.get("additions") or 0)
    deletions = int(node.get("deletions") or 0)
    return paths, additions, deletions, not has_next


def count_armed_open_pull_requests(nodes: Sequence[Mapping[str, Any]]) -> int:
    """Count open PRs that actually have auto-merge enabled (#4265)."""
    return sum(1 for node in nodes if isinstance(node, Mapping) and node.get("autoMergeRequest") is not None)


def stitch_armed_pull_request_pages(pages: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Concatenate armed ``pullRequests`` pages into one connection block (#4265)."""
    nodes: list[Mapping[str, Any]] = []
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        raw_nodes = page.get("nodes") or []
        if isinstance(raw_nodes, list):
            nodes.extend(n for n in raw_nodes if isinstance(n, Mapping))
    last = pages[-1] if pages else {}
    page_info = last.get("pageInfo") if isinstance(last, Mapping) else {}
    if not isinstance(page_info, Mapping):
        page_info = {}
    return {
        "totalCount": len(nodes),
        "nodes": nodes,
        "pageInfo": page_info,
    }


def armed_pull_requests_has_next_page(armed_block: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Return (hasNextPage, endCursor) from an armed PR GraphQL connection."""
    page_info = armed_block.get("pageInfo")
    if not isinstance(page_info, Mapping):
        return False, None
    has_next = bool(page_info.get("hasNextPage"))
    cursor = page_info.get("endCursor")
    end_cursor = cursor if isinstance(cursor, str) and cursor else None
    return has_next, end_cursor


def graphql_nodes(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], int, int]:
    """Return open PR nodes, armed count, merge-queue depth from one GraphQL response."""
    repo = payload.get("repository") if isinstance(payload, Mapping) else {}
    if not isinstance(repo, Mapping):
        return [], 0, 0
    mq = (repo.get("mergeQueue") or {}).get("entries") or {}
    queue_depth = int(mq.get("totalCount") or 0) if isinstance(mq, Mapping) else 0
    armed_block = repo.get("armedPullRequests") or {}
    armed_nodes = (armed_block.get("nodes") or []) if isinstance(armed_block, Mapping) else []
    armed_models_filtered = [n for n in armed_nodes if isinstance(n, Mapping)]
    armed_count = count_armed_open_pull_requests(armed_models_filtered)
    pr_block = repo.get("pullRequests") or {}
    nodes = (pr_block.get("nodes") or []) if isinstance(pr_block, Mapping) else []
    return [n for n in nodes if isinstance(n, Mapping)], armed_count, queue_depth


def hub_hold_blocks_pr(
    *,
    repo: str,
    pr_number: int,
    holds: Sequence[Mapping[str, Any]],
) -> str:
    """Return a reason when an active schedule hold on the hub names this PR."""
    repo_name = repo.rsplit("/", 1)[-1].lower()
    for hold in holds:
        if not hold.get("active", True):
            continue
        if str(hold.get("kind") or "schedule") != "schedule":
            continue
        applies = [str(a).strip() for a in (hold.get("applies_to") or [])]
        if not any(a == "*" or a.lower() == f"repo:{repo_name}" for a in applies):
            continue
        text = str(hold.get("text") or "")
        if re.search(rf"(?:PR\s*#|#){pr_number}\b", text, re.IGNORECASE):
            return "hub_hold"
    return ""


def pre_filter_skip_reason(
    node: Mapping[str, Any],
    *,
    repo: str,
    skip_list: frozenset[tuple[str, int]],
    holds: Sequence[Mapping[str, Any]],
) -> str:
    number = int(node.get("number") or 0)
    if (repo, number) in skip_list:
        return "config_skip"
    label_hold = label_blocks_shepherd(node)
    if label_hold:
        return label_hold
    if str(node.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        return "changes_requested"
    merge_state = str(node.get("mergeStateStatus") or "").upper()
    if merge_state and merge_state != "CLEAN":
        return f"merge_state_{merge_state.lower()}"
    checks = _checks_state(node)
    if checks == "FAILURE":
        return "checks_failed"
    if checks == "ERROR":
        return "checks_error"
    if hub_hold_blocks_pr(repo=repo, pr_number=number, holds=holds):
        return "hub_hold"
    return ""


def node_to_candidate(repo: str, node: Mapping[str, Any], *, is_drained: bool) -> PRCandidate:
    number = int(node["number"])
    mergeable = str(node.get("mergeable") or "UNKNOWN").upper()
    merge_state = str(node.get("mergeStateStatus") or "").upper()
    is_behind = merge_state == "BEHIND"
    if mergeable == "CONFLICTING":
        mergeable = "CONFLICTING"
    elif merge_state == "DIRTY":
        mergeable = "CONFLICTING"
    checks = _checks_state(node)
    if checks == "SUCCESS":
        checks_status = "SUCCESS"
    elif checks in {"EXPECTED", "PENDING"}:
        checks_status = "PENDING"
    else:
        checks_status = "FAILURE"
    is_queued = (node.get("mergeQueueEntry") or {}).get("position") is not None
    auto_merge = node.get("autoMergeRequest") is not None
    return PRCandidate(
        repo=repo,
        pr_number=number,
        title=str(node.get("title") or ""),
        is_draft=bool(node.get("isDraft")),
        mergeable=mergeable,
        is_behind=is_behind,
        checks_status=checks_status,
        labels=_label_names(node),
        is_queued=is_queued,
        auto_merge_enabled=auto_merge,
        is_drained=is_drained,
    )


def normalize_pass_decision(decision: ShepherdDecision) -> ShepherdDecision:
    """Shepherd pass never updates branches (#Bob merge-rate)."""
    if decision.action == ShepherdAction.UPDATE_BRANCH:
        return ShepherdDecision(
            ShepherdAction.SKIP,
            decision.pr_number,
            decision.repo,
            reason=decision.reason,
            detail="behind base; shepherd pass does not update branches",
            priority=decision.priority,
        )
    return decision


def plan_repo_pass(
    repo: str,
    nodes: Sequence[Mapping[str, Any]],
    *,
    skip_list: frozenset[tuple[str, int]],
    holds: Sequence[Mapping[str, Any]],
    armed_count: int,
    queue_depth: int,
    live: bool,
    line_cap: int | None = None,
) -> tuple[list[PassAction], list[dict[str, Any]], bool]:
    """Plan arm actions for one repo. Returns actions, skip audit rows, backoff flag."""
    if armed_count + queue_depth > QUEUE_BACKOFF_THRESHOLD:
        return [], [], True
    effective_line_cap = line_cap if line_cap is not None else default_low_risk_line_cap()
    is_drained = False
    candidates: list[PRCandidate] = []
    skip_rows: list[dict[str, Any]] = []
    eligibility_by_pr: dict[int, str] = {}
    for node in nodes:
        if node.get("isDraft"):
            continue
        pre = pre_filter_skip_reason(node, repo=repo, skip_list=skip_list, holds=holds)
        if pre:
            skip_rows.append({"repo": repo, "pr": int(node["number"]), "reason": pre})
            continue
        paths, additions, deletions, files_complete = _pr_file_metadata(node)
        eligibility, eligibility_detail = arm_eligibility_reason(
            review_decision=str(node.get("reviewDecision") or ""),
            file_paths=paths,
            additions=additions,
            deletions=deletions,
            line_cap=effective_line_cap,
            labels=_label_names(node),
            checks_state=_checks_state(node),
            files_complete=files_complete,
        )
        number = int(node["number"])
        if eligibility == "no_approval":
            skip_rows.append(
                {
                    "repo": repo,
                    "pr": number,
                    "reason": f"no_approval:{eligibility_detail}",
                }
            )
            continue
        eligibility_by_pr[number] = eligibility
        candidates.append(node_to_candidate(repo, node, is_drained=is_drained))
    decisions = [normalize_pass_decision(d) for d in rank_candidates_for_shepherding(candidates)]
    cap = _repo_arm_cap(repo)
    actions: list[PassAction] = []
    for decision in decisions:
        if decision.action != ShepherdAction.AUTO_ARM:
            if decision.action == ShepherdAction.SKIP:
                skip_rows.append(
                    {
                        "repo": repo,
                        "pr": decision.pr_number,
                        "reason": (decision.reason.value if decision.reason else decision.detail or "skip"),
                    }
                )
            continue
        if len(actions) >= cap:
            skip_rows.append({"repo": repo, "pr": decision.pr_number, "reason": "per_pass_cap"})
            continue
        head = ""
        for node in nodes:
            if int(node.get("number") or 0) == decision.pr_number:
                head = str(node.get("headRefOid") or "")
                break
        flag = decision.merge_flag or get_auto_merge_flag(repo)
        strategy = "squash" if "squash" in flag else "merge"
        actions.append(
            PassAction(
                repo=repo,
                pr_number=decision.pr_number,
                head_sha=head,
                strategy=strategy,
                dry_run=not live,
                detail=decision.detail,
                eligibility=eligibility_by_pr.get(decision.pr_number, ""),
            )
        )
    return actions, skip_rows, False


def run_merge_shepherd_pass(
    *,
    config: PassConfig,
    fetch_graphql: Callable[[str, str], Mapping[str, Any]],
    holds: Sequence[Mapping[str, Any]] | None = None,
) -> PassSummary:
    """Execute a read-only pass over fleet repos and return a JSON-serializable summary."""
    skip_path = config.skip_path or default_skip_path(config.repo_root)
    skip_list = load_skip_list(skip_path)
    hold_rows = list(holds or ())
    summary = PassSummary()
    for full in SHEPHERD_REPOS:
        owner, name = full.split("/", 1)
        try:
            payload = fetch_graphql(owner, name)
        except OSError as exc:
            summary.errors.append(f"{full}: {exc}")
            continue
        nodes, armed_count, queue_depth = graphql_nodes(payload)
        actions, skips, backoff = plan_repo_pass(
            full,
            nodes,
            skip_list=skip_list,
            holds=hold_rows,
            armed_count=armed_count,
            queue_depth=queue_depth,
            live=config.live and not config.dry_run,
            line_cap=config.low_risk_line_cap,
        )
        repo_row = {
            "repo": full,
            "open_prs": len(nodes),
            "armed_count": armed_count,
            "queue_depth": queue_depth,
            "planned_arms": len(actions),
            "skipped": len(skips),
        }
        summary.repos.append(repo_row)
        summary.skipped.extend(skips)
        if backoff:
            summary.backoff.append(full)
            continue
        for act in actions:
            summary.armed.append(
                {
                    "repo": act.repo,
                    "pr": act.pr_number,
                    "head_sha": act.head_sha,
                    "strategy": act.strategy,
                    "dry_run": act.dry_run,
                    "detail": act.detail,
                    "eligibility": act.eligibility,
                }
            )
    return summary


def summary_to_json(summary: PassSummary) -> dict[str, Any]:
    return {
        "repos": summary.repos,
        "armed": summary.armed,
        "skipped": summary.skipped,
        "backoff": summary.backoff,
        "errors": summary.errors,
    }
