"""GitHub webhook events → flat CI-fix triggers (RD-1, #1881 / #1879).

Pure functions only: signature verification and payload parsing. No network, no state.

Accepted events (everything else is ignored with a reason):

- ``workflow_run`` / ``completed`` with conclusion ``failure`` or ``timed_out`` on a run tied
  to a pull request, including ``merge_group`` runs whose ``pull_requests`` is empty (the PR
  number comes from the ``gh-readonly-queue/<base>/pr-<N>-<sha>`` head ref).
  ``cancelled`` is not a failure: fleet workflows cancel superseded runs by concurrency group.
- ``pull_request`` / ``dequeued`` with reason ``MERGE_CONFLICT`` or ``CHECKS_FAILED``
  (``PullRequestDequeuedEvent`` in ``@octokit/webhooks-types``; ``reason`` is a free-form
  string there, so case, spaces and hyphens are normalised).

``check_suite`` is deliberately ignored: every Actions failure already arrives as a
``workflow_run``, and accepting both would double-trigger the same failure.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Literal

from ci_fix_dispatch import QUEUE_REF_PATTERN, pr_number_from_queue_ref
from dashboard_config import ORG
from pydantic import BaseModel, ConfigDict, Field

GITHUB_WEBHOOK_SECRET_ENV = "GITHUB_WEBHOOK_SECRET"
CI_FIX_ENABLED_ENV = "CI_FIX_DISPATCH_ENABLED"
FAILED_CONCLUSIONS = ("failure", "timed_out")
DEQUEUE_KINDS: dict[str, Literal["merge_conflict", "queue_checks_failed"]] = {
    "MERGE_CONFLICT": "merge_conflict",
    "CHECKS_FAILED": "queue_checks_failed",
}

EventKind = Literal["ci_failure", "merge_conflict", "queue_checks_failed"]


class CIFixEvent(BaseModel):
    """One actionable CI-fix trigger, flattened from a GitHub webhook payload (LoD)."""

    model_config = ConfigDict(frozen=True)

    kind: EventKind
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    pr_number: int = Field(gt=0)
    head_branch: str = ""
    base_ref: str = ""
    run_id: int | None = None
    workflow_name: str = ""
    conclusion: str = ""
    from_queue: bool = False
    # Known only when the payload carries the PR (``dequeued``); otherwise fetched.
    is_draft: bool | None = None
    pr_state: str = ""
    # ``owner/name`` the PR head lives in; a fork head cannot be pushed to (#1887 review).
    head_repo: str = ""

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"


def ci_fix_dispatch_enabled() -> bool:
    """The live-trigger feature flag (default off; reversible without a deploy)."""
    return os.environ.get(CI_FIX_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time check of ``X-Hub-Signature-256`` (``sha256=<hex HMAC of the raw body>``)."""
    assert secret, "callers must reject requests before verifying when no secret is configured"  # noqa: S101
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _repo_identity(payload: dict[str, Any]) -> tuple[str, str]:
    repo = payload.get("repository") or {}
    owner = str((repo.get("owner") or {}).get("login") or str(repo.get("full_name") or "").partition("/")[0])
    return owner, str(repo.get("name") or "")


def _ref(obj: Any) -> str:
    return str(obj.get("ref") or "") if isinstance(obj, dict) else ""


def _full_name(obj: Any) -> str:
    return str(obj.get("full_name") or "") if isinstance(obj, dict) else ""


def _normalise_reason(reason: Any) -> str:
    return str(reason or "").strip().upper().replace("-", "_").replace(" ", "_")


def _parse_workflow_run(payload: dict[str, Any], owner: str, repo: str) -> tuple[CIFixEvent | None, str]:
    if payload.get("action") != "completed":
        return None, f"workflow_run action {payload.get('action')!r} is not 'completed'"
    run = payload.get("workflow_run") or {}
    conclusion = str(run.get("conclusion") or "").lower()
    if conclusion not in FAILED_CONCLUSIONS:
        return None, f"run conclusion {conclusion!r} is not a failure"
    head_branch = str(run.get("head_branch") or "")
    queue_match = QUEUE_REF_PATTERN.match(head_branch)
    prs = [p for p in run.get("pull_requests") or [] if isinstance(p, dict) and p.get("number")]
    pr_number = int(prs[0]["number"]) if prs else pr_number_from_queue_ref(head_branch)
    if not pr_number:
        return None, "run is not associated with a pull request"
    base_ref = queue_match.group("base") if queue_match else (_ref(prs[0].get("base")) if prs else "")
    event = CIFixEvent(
        kind="ci_failure",
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        head_branch=head_branch,
        base_ref=base_ref,
        run_id=int(run["id"]) if run.get("id") else None,
        workflow_name=str(run.get("name") or ""),
        conclusion=conclusion,
        from_queue=bool(queue_match) or run.get("event") == "merge_group",
        head_repo=_full_name(run.get("head_repository")),
    )
    return event, "accepted"


def _parse_dequeued(payload: dict[str, Any], owner: str, repo: str) -> tuple[CIFixEvent | None, str]:
    if payload.get("action") != "dequeued":
        return None, f"pull_request action {payload.get('action')!r} is not 'dequeued'"
    reason = _normalise_reason(payload.get("reason"))
    kind = DEQUEUE_KINDS.get(reason)
    pr = payload.get("pull_request") or {}
    if kind is None:
        return None, f"dequeue reason {reason or 'unknown'!r} needs no CI fix"
    if not pr.get("number"):
        return None, "dequeued event carries no pull request number"
    event = CIFixEvent(
        kind=kind,
        owner=owner,
        repo=repo,
        pr_number=int(pr["number"]),
        head_branch=_ref(pr.get("head")),
        base_ref=_ref(pr.get("base")) or "main",
        conclusion=reason,
        from_queue=True,
        is_draft=bool(pr.get("draft", False)),
        pr_state=str(pr.get("state") or ""),
        head_repo=_full_name((pr.get("head") or {}).get("repo")),
    )
    return event, "accepted"


def parse_github_event(event_name: str, payload: dict[str, Any], *, org: str = ORG) -> tuple[CIFixEvent | None, str]:
    """Map a webhook delivery to a :class:`CIFixEvent`, or ``(None, reason)`` when it needs no fix.

    Pre: ``payload`` is the decoded JSON body of a signature-verified delivery.
    Post: a returned event names a repository of ``org`` and a positive PR number.
    """
    if event_name == "ping":
        return None, "pong"
    if event_name not in ("workflow_run", "pull_request"):
        return None, f"event {event_name!r} is not handled (check_suite is covered by workflow_run)"
    owner, repo = _repo_identity(payload)
    if not owner or not repo:
        return None, "payload has no repository"
    if owner.lower() != org.lower():
        return None, f"repository owner {owner!r} is not the configured org {org!r}"
    if event_name == "workflow_run":
        return _parse_workflow_run(payload, owner, repo)
    return _parse_dequeued(payload, owner, repo)
