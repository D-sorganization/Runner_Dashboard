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
_PR_REF = re.compile(r"\b(?:PR\s*#|#)(\d+)\b", re.IGNORECASE)

OPEN_PRS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    mergeQueue {
      entries(first: 100) { totalCount }
    }
    armedPullRequests: pullRequests(states: OPEN, first: 100) {
      totalCount
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
        labels(first: 30) { nodes { name } }
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


@dataclass(frozen=True)
class PassAction:
    repo: str
    pr_number: int
    head_sha: str
    strategy: str
    dry_run: bool
    detail: str


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
    commits = ((node.get("commits") or {}).get("nodes") or [])
    if not commits:
        return "UNKNOWN"
    commit = commits[0].get("commit") if isinstance(commits[0], Mapping) else {}
    rollup = (commit or {}).get("statusCheckRollup") if isinstance(commit, Mapping) else {}
    return str((rollup or {}).get("state") or "UNKNOWN").upper()


def _label_names(node: Mapping[str, Any]) -> tuple[str, ...]:
    labels = ((node.get("labels") or {}).get("nodes") or [])
    return tuple(str(n.get("name") or "") for n in labels if isinstance(n, Mapping) and n.get("name"))


def graphql_nodes(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], int, int]:
    """Return open PR nodes, armed count, merge-queue depth from one GraphQL response."""
    repo = payload.get("repository") if isinstance(payload, Mapping) else {}
    if not isinstance(repo, Mapping):
        return [], 0, 0
    mq = (repo.get("mergeQueue") or {}).get("entries") or {}
    queue_depth = int(mq.get("totalCount") or 0) if isinstance(mq, Mapping) else 0
    armed_block = repo.get("armedPullRequests") or {}
    armed_nodes = (armed_block.get("nodes") or []) if isinstance(armed_block, Mapping) else []
    armed_count = int(armed_block.get("totalCount") or len(armed_nodes)) if isinstance(armed_block, Mapping) else 0
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
) -> tuple[list[PassAction], list[dict[str, Any]], bool]:
    """Plan arm actions for one repo. Returns actions, skip audit rows, backoff flag."""
    if armed_count + queue_depth > QUEUE_BACKOFF_THRESHOLD:
        return [], [], True
    is_drained = False
    candidates: list[PRCandidate] = []
    skip_rows: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("isDraft"):
            continue
        pre = pre_filter_skip_reason(node, repo=repo, skip_list=skip_list, holds=holds)
        if pre:
            skip_rows.append({"repo": repo, "pr": int(node["number"]), "reason": pre})
            continue
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
