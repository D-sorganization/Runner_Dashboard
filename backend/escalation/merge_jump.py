"""Merge-queue jump via ``enqueuePullRequest(jump: true)`` (slice 3, #2952)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from dashboard_config import ORG
from staff.holds import HoldsList

HOLD_LABEL = "hold"
_JUMP_MUTATION = (
    "mutation($id: ID!, $oid: GitObjectID!) { enqueuePullRequest(input: "
    "{pullRequestId: $id, expectedHeadOid: $oid, jump: true}) "
    "{ mergeQueueEntry { id position jump } } } }"
)
_QUEUE_DEPTH_QUERY = (
    "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) "
    "{ mergeQueue { entries(first: 1) { totalCount } } } } }"
)
_PERMISSION_MARKERS = (
    "must have admin rights",
    "administrator",
    "bypass",
    "insufficient",
    "resource not accessible",
    "forbidden",
)
_RULESET_BYPASS_NOTE = (
    "Merge-queue jump requires a ruleset bypass actor with merge-queue permission "
    "(Runner_Dashboard ruleset 24444089). Without that actor, GitHub may accept "
    "enqueuePullRequest(jump: true) but leave the PR at the back of the queue."
)


@dataclass(frozen=True)
class EscalatePlan:
    owner: str
    repo: str
    pr: int
    node_id: str
    head_sha: str


@dataclass(frozen=True)
class EscalateResult:
    ok: bool
    message: str
    manual_command: str = ""
    detail: dict[str, Any] | None = None
    jump_ignored: bool = False


def split_repo(slug: str) -> tuple[str, str]:
    slug = slug.strip()
    if "/" in slug:
        owner, repo = slug.split("/", 1)
        return owner, repo
    return ORG, slug


def personal_jump_command(plan: EscalatePlan) -> str:
    """Command for Dieter/Barb to run under ``gh-personal`` when the fleet token cannot jump."""
    q = _JUMP_MUTATION.replace('"', '\\"')
    return f'gh-personal api graphql -f query="{q}" -f id={plan.node_id} -f oid={plan.head_sha}'


def _label_names(labels: list[dict[str, Any]]) -> set[str]:
    return {str(label.get("name", "")).lower() for label in labels if isinstance(label, dict)}


def refusal_from_pr_payload(payload: dict[str, Any], repo_name: str) -> str | None:
    if str(payload.get("state", "")).lower() != "open":
        return "PR is not open"
    labels = _label_names(payload.get("labels") or [])
    if HOLD_LABEL in labels:
        return "PR carries the hold label"
    hold = HoldsList().blocking("*", repo_name)
    if hold is not None:
        return f"Staff hold blocks dispatch: {hold.text}"
    mergeable_state = str(payload.get("mergeable_state") or "").lower()
    if mergeable_state in {"dirty", "blocked"}:
        return f"Required checks are not green (mergeable_state={mergeable_state})"
    return None


def plan_from_pr_view(payload: dict[str, Any], owner: str, repo: str, pr: int) -> EscalatePlan:
    node_id = str(payload.get("id") or "")
    head_raw = payload.get("head")
    head = head_raw if isinstance(head_raw, dict) else {}
    head_sha = str(head.get("sha") or "")
    if not node_id or not head_sha:
        raise ValueError("GitHub PR payload missing id or head.sha")
    return EscalatePlan(owner=owner, repo=repo, pr=pr, node_id=node_id, head_sha=head_sha)


def interpret_enqueue_error(stderr: str, stdout: str, plan: EscalatePlan) -> EscalateResult:
    text = f"{stderr}\n{stdout}".lower()
    if any(marker in text for marker in _PERMISSION_MARKERS):
        return EscalateResult(
            ok=False,
            message=(
                "Fleet GitHub token cannot jump the merge queue (admin or merge-queue bypass required). "
                "Jumping rebuilds in-flight queue entries."
            ),
            manual_command=personal_jump_command(plan),
        )
    return EscalateResult(ok=False, message=(stderr or stdout or "enqueuePullRequest failed").strip())


def parse_enqueue_success(stdout: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    entry = (data.get("enqueuePullRequest") or {}).get("mergeQueueEntry")
    return entry if isinstance(entry, dict) else None


def parse_queue_depth(stdout: str) -> int | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    repo = (payload.get("data") or {}).get("repository") if isinstance(payload, dict) else None
    if not isinstance(repo, dict):
        return None
    entries = (repo.get("mergeQueue") or {}).get("entries")
    if not isinstance(entries, dict):
        return None
    total = entries.get("totalCount")
    return int(total) if total is not None else None


def verify_jump_took_effect(
    entry: dict[str, Any] | None,
    *,
    queue_depth: int | None,
    plan: EscalatePlan,
) -> EscalateResult | None:
    """Return a failure result when the mutation succeeded but jump did not move the PR to the front."""
    if not entry:
        return EscalateResult(
            ok=False,
            jump_ignored=True,
            message="enqueuePullRequest succeeded but returned no mergeQueueEntry; cannot verify jump.",
            detail=None,
        )
    position_raw = entry.get("position")
    if position_raw is None:
        return EscalateResult(
            ok=False,
            jump_ignored=True,
            message="enqueuePullRequest succeeded but mergeQueueEntry.position is missing; cannot verify jump.",
            detail=entry,
        )
    position = int(position_raw)
    depth_suffix = f" of {queue_depth}" if queue_depth is not None else ""
    at_back = queue_depth is not None and queue_depth > 1 and position >= queue_depth
    if position <= 1 and not at_back:
        return None

    return EscalateResult(
        ok=False,
        jump_ignored=True,
        message=(
            f"enqueuePullRequest succeeded but jump did not take effect: merge queue position "
            f"{position}{depth_suffix}. {_RULESET_BYPASS_NOTE}"
        ),
        detail={**entry, "queue_depth": queue_depth},
        manual_command=personal_jump_command(plan),
    )


def success_message(entry: dict[str, Any], queue_depth: int | None) -> str:
    position = int(entry.get("position") or 1)
    depth_suffix = f" of {queue_depth}" if queue_depth is not None else ""
    return (
        f"PR enqueued at merge queue position {position}{depth_suffix}. "
        "Jumping rebuilds in-flight merge queue entries (GitHub merge-queue docs)."
    )


async def fetch_merge_queue_depth(plan: EscalatePlan) -> int | None:
    from system_utils import run_cmd

    code, stdout, _stderr = await run_cmd(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_QUEUE_DEPTH_QUERY}",
            "-f",
            f"owner={plan.owner}",
            "-f",
            f"name={plan.repo}",
        ],
        timeout=60,
    )
    if code != 0:
        return None
    return parse_queue_depth(stdout)


async def enqueue_jump(plan: EscalatePlan) -> EscalateResult:
    from system_utils import run_cmd

    code, stdout, stderr = await run_cmd(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_JUMP_MUTATION}",
            "-f",
            f"id={plan.node_id}",
            "-f",
            f"oid={plan.head_sha}",
        ],
        timeout=60,
    )
    if code != 0:
        return interpret_enqueue_error(stderr, stdout, plan)

    entry = parse_enqueue_success(stdout)
    queue_depth = await fetch_merge_queue_depth(plan)
    ignored = verify_jump_took_effect(entry, queue_depth=queue_depth, plan=plan)
    if ignored is not None:
        return ignored
    assert entry is not None
    return EscalateResult(
        ok=True,
        message=success_message(entry, queue_depth),
        detail={**entry, "queue_depth": queue_depth},
    )
