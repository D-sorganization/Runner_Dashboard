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
_PERMISSION_MARKERS = (
    "must have admin rights",
    "administrator",
    "bypass",
    "insufficient",
    "resource not accessible",
    "forbidden",
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


def split_repo(slug: str) -> tuple[str, str]:
    slug = slug.strip()
    if "/" in slug:
        owner, repo = slug.split("/", 1)
        return owner, repo
    return ORG, slug


def personal_jump_command(plan: EscalatePlan) -> str:
    """Command for Dieter/Barb to run under ``gh-personal`` when the fleet token cannot jump."""
    q = _JUMP_MUTATION.replace('"', '\\"')
    return (
        f"gh-personal api graphql -f query=\"{q}\" "
        f"-f id={plan.node_id} -f oid={plan.head_sha}"
    )


def _label_names(labels: list[dict[str, Any]]) -> set[str]:
    return {str(l.get("name", "")).lower() for l in labels if isinstance(l, dict)}


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
    head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
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
    if code == 0:
        entry = parse_enqueue_success(stdout)
        return EscalateResult(
            ok=True,
            message=(
                "PR enqueued at the front of the merge queue. "
                "Jumping rebuilds in-flight queue entries (GitHub merge-queue docs)."
            ),
            detail=entry,
        )
    return interpret_enqueue_error(stderr, stdout, plan)


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
