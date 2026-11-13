"""Pre-dispatch premise check for task, issue, and follow-up chip dispatches.

Part of Repository_Management#1889 / Issue #1847 (RD-2), hardened in #4260.

Before dispatching any agent session from an issue, a follow-up chip or `spawn_task`:
1. If the task names a failing check via `failing-check: <name>`, query that check on current
   `main` (GitHub check-runs REST or an allowlisted local script). If it passes, annotate
   the skip in the dispatch audit; never auto-close the issue.
2. If an open PR already references the issue, or an active `claim:*` lease exists,
   don't dispatch.
3. Log every skip with its reason in the dispatch audit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from time_utils import utc_now_iso

log = logging.getLogger("dashboard.dispatch.premise")

_FAILING_CHECK_MARKER = re.compile(r"^\s*failing-check:\s*(?P<name>[^\s#]+)", re.I | re.M)

# Allowlisted local re-check commands keyed by normalized check name (#4260).
ALLOWED_LOCAL_PREMISE_CHECKS: dict[str, list[str]] = {
    "check_law_of_demeter": ["python3", "scripts/ci/check_law_of_demeter.py"],
    "check_dry_duplication_gate": ["python3", "scripts/ci/check_dry_duplication_gate.py"],
    "check_architecture_budget": ["python3", "scripts/ci/check_architecture_budget.py"],
    "check_conflict_markers": ["git", "grep", "-n", "<<<<<<<"],
}

# ─── Audited No-Op Prompts Replay Register ────────────────────────────────────

AUDITED_NO_OP_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "lod-baseline",
        "repository": "D-sorganization/Runner_Dashboard",
        "issue": 1720,
        "prompt": "failing-check: check_law_of_demeter\nFix Law of Demeter violations on branch",
        "description": "the LoD baseline had already been fixed on the branch",
    },
    {
        "id": "conflict-marker",
        "repository": "D-sorganization/Runner_Dashboard",
        "issue": 1740,
        "prompt": "failing-check: check_conflict_markers\nRemove stray conflict marker in docs/HANDOFF.md",
        "description": "the stray conflict marker had already been removed by #1739, 39 minutes earlier",
    },
    {
        "id": "local-only-runner-guard",
        "repository": "D-sorganization/AffineDrift",
        "issue": 4200,
        "prompt": "failing-check: local-only-runner-guard\nFix failing workflow on AffineDrift",
        "description": "the AffineDrift local-only-runner-guard failure had been fixed 7 weeks earlier",
    },
]


@dataclass(frozen=True, slots=True)
class PremiseCheckResult:
    """Outcome of a pre-dispatch premise check."""

    allowed: bool
    reason: str = ""
    detail: str = ""
    comment: str = ""
    should_close: bool = False
    head_sha: str = ""
    check_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "detail": self.detail,
            "comment": self.comment,
            "should_close": self.should_close,
            "head_sha": self.head_sha,
            "check_name": self.check_name,
        }


def _normalize_check_name(name: str) -> str:
    stripped = name.strip().lower()
    for suffix in (".yml", ".yaml"):
        if stripped.endswith(suffix):
            return stripped[: -len(suffix)]
    return stripped


def extract_failing_check_premise(text: str) -> dict[str, Any] | None:
    """Return premise info only when an explicit ``failing-check:`` marker is present."""
    if not text:
        return None
    match = _FAILING_CHECK_MARKER.search(text)
    if not match:
        return None
    raw_name = match.group("name").strip()
    check_name = _normalize_check_name(raw_name)
    check_type = "workflow" if raw_name.lower().endswith((".yml", ".yaml")) else "check_run"
    return {
        "check_name": check_name,
        "check_type": check_type,
        "command": "",
    }


# ─── Open PR & Claim Checks ───────────────────────────────────────────────────


def _normalize_repo(repo: str) -> str:
    parts = repo.strip().split("/")
    return f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else f"D-sorganization/{parts[-1]}"


async def check_issue_has_open_pr(
    repository: str,
    issue_number: int,
    *,
    gh_get_fn: Any = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Return True if an open PR already references this issue."""
    full_repo = _normalize_repo(repository)
    endpoint = f"/repos/{full_repo}/pulls?state=open&per_page=100"

    pulls: list[dict[str, Any]] = []
    if gh_get_fn:
        res = await gh_get_fn(endpoint)
        pulls = res if isinstance(res, list) else []
    else:
        try:
            from gh_utils import gh_api  # noqa: PLC0415

            res = await gh_api(endpoint)
            pulls = res if isinstance(res, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to query open PRs for %s: %s", full_repo, exc)
            return False, None

    issue_ref = str(issue_number)
    closing_ref = re.compile(rf"\b(?:close[sd]?|fix(?:es|ed)?|resolv(?:e[sd]?))\s+#\s*{issue_ref}\b", re.I)
    branch_ref = re.compile(rf"(?:^|[/_-])issue-?{issue_ref}(?:$|[-_])|(?:^|[/_-]){issue_ref}(?:$|[-_])", re.I)

    for pr in pulls:
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        head_ref = str((pr.get("head") or {}).get("ref") or "")
        closing_refs = pr.get("closingIssuesReferences") or pr.get("closing_issues_references") or []
        if isinstance(closing_refs, dict):
            closing_refs = closing_refs.get("nodes") or []
        closes_issue = any(isinstance(ref, dict) and str(ref.get("number")) == issue_ref for ref in closing_refs)
        if closes_issue or closing_ref.search(body) or branch_ref.search(head_ref):
            return True, {
                "number": pr.get("number"),
                "title": title,
                "url": pr.get("html_url") or pr.get("url"),
            }

    return False, None


async def check_issue_has_active_claim(
    repository: str,
    issue_number: int,
    *,
    gh_get_fn: Any = None,
    lease_mgr: Any = None,
    check_labels: bool = True,
) -> tuple[bool, str]:
    """Return True if an active claim:* lease or label exists for the issue.

    ``check_labels=False`` skips the GitHub label read (staff dispatch does it in
    ``staff.claim_guard``, #2562) and keeps the local runner-lease check.
    """
    full_repo = _normalize_repo(repository)
    endpoint = f"/repos/{full_repo}/issues/{issue_number}"

    issue: dict[str, Any] = {}
    if not check_labels:
        pass
    elif gh_get_fn:
        res = await gh_get_fn(endpoint)
        issue = res if isinstance(res, dict) else {}
    else:
        try:
            from gh_utils import gh_api  # noqa: PLC0415

            res = await gh_api(endpoint)
            issue = res if isinstance(res, dict) else {}
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to query issue %s#%d: %s", full_repo, issue_number, exc)

    labels = issue.get("labels") or []
    for lbl in labels:
        name = lbl.get("name") if isinstance(lbl, dict) else str(lbl)
        if name and name.startswith("claim:"):
            return True, f"issue #{issue_number} has active claim label '{name}'"

    if lease_mgr is None:
        try:
            from runner_lease import lease_manager  # noqa: PLC0415

            lease_mgr = lease_manager
        except ImportError:
            lease_mgr = None

    if lease_mgr and hasattr(lease_mgr, "active_leases"):
        try:
            for lease in lease_mgr.active_leases.values():
                meta = getattr(lease, "metadata", {}) or {}
                if meta.get("repo") == full_repo and meta.get("number") == issue_number:
                    return True, f"issue #{issue_number} has active runner lease '{lease.lease_id}'"
        except Exception as exc:  # noqa: BLE001
            log.debug("Lease manager check bypassed: %s", exc)

    return False, ""


# ─── Re-check Premise on Main ─────────────────────────────────────────────────


async def _check_runs_on_main(
    full_repo: str,
    check_name: str,
    gh_get_fn: Any,
) -> tuple[bool | None, str]:
    """Query check runs on main branch via GitHub REST API."""
    endpoint = f"/repos/{full_repo}/commits/main/check-runs"
    try:
        if gh_get_fn:
            res = await gh_get_fn(endpoint)
        else:
            from gh_utils import gh_api  # noqa: PLC0415

            res = await gh_api(endpoint)
    except Exception as exc:  # noqa: BLE001
        log.warning("Check-runs query on %s failed: %s", full_repo, exc)
        return None, ""

    if not isinstance(res, dict):
        return None, ""

    check_runs = res.get("check_runs") or []
    head_sha = ""
    target_name = _normalize_check_name(check_name)
    for cr in check_runs:
        cr_name = _normalize_check_name(str(cr.get("name") or ""))
        if not head_sha:
            head_sha = str(cr.get("head_sha") or "")[:7]
        if cr_name != target_name:
            continue
        status = cr.get("status")
        conclusion = cr.get("conclusion")
        if status == "completed":
            return conclusion == "success", head_sha

    return None, head_sha


async def recheck_premise_on_main(
    repository: str,
    check_info: dict[str, Any],
    *,
    gh_get_fn: Any = None,
    run_cmd_fn: Any = None,
    repo_root: Path | None = None,
) -> tuple[bool, str]:
    """Re-run or query a check against current main.

    Returns (passed, head_sha).
    """
    full_repo = _normalize_repo(repository)
    check_name = check_info["check_name"]

    passed, head_sha = await _check_runs_on_main(full_repo, check_name, gh_get_fn)
    if passed is not None:
        return passed, head_sha or "main"

    cmd_args = ALLOWED_LOCAL_PREMISE_CHECKS.get(check_name)
    if cmd_args and run_cmd_fn:
        try:
            cwd = repo_root or Path.cwd()
            code, _stdout, _stderr = await run_cmd_fn(cmd_args, timeout=30, cwd=cwd)
            return code == 0, head_sha or "main"
        except Exception as exc:  # noqa: BLE001
            log.warning("Allowlisted check command %s failed to run: %s", cmd_args, exc)

    return False, head_sha or "main"


# ─── Premise Evaluator & Actions ──────────────────────────────────────────────


async def evaluate_dispatch_premise(
    repository: str,
    issue_number: int | None = None,
    prompt: str = "",
    *,
    gh_get_fn: Any = None,
    run_cmd_fn: Any = None,
    lease_mgr: Any = None,
    repo_root: Path | None = None,
    issue_body: str = "",
    check_claim_labels: bool = True,
    check_open_pr: bool = True,
) -> PremiseCheckResult:
    """Evaluate whether a task dispatch premise is valid or should be skipped.

    Enforces:
    - Skip if active claim:* lease or label exists.
    - Skip if an open PR references the issue.
    - Skip if named failing check (structured marker only) already passes on current main.
    """
    full_repo = _normalize_repo(repository)

    if issue_number:
        has_claim, claim_detail = await check_issue_has_active_claim(
            full_repo,
            issue_number,
            gh_get_fn=gh_get_fn,
            lease_mgr=lease_mgr,
            check_labels=check_claim_labels,
        )
        if has_claim:
            return PremiseCheckResult(
                allowed=False,
                reason="active_claim_exists",
                detail=claim_detail,
            )

    if issue_number and check_open_pr:
        has_pr, pr_info = await check_issue_has_open_pr(
            full_repo,
            issue_number,
            gh_get_fn=gh_get_fn,
        )
        if has_pr and pr_info:
            return PremiseCheckResult(
                allowed=False,
                reason="open_pr_exists",
                detail=f"open PR #{pr_info['number']} ('{pr_info['title']}') already references issue #{issue_number}",
            )

    full_text = f"{prompt}\n{issue_body}".strip()
    check_info = extract_failing_check_premise(full_text)
    if check_info:
        passed, head_sha = await recheck_premise_on_main(
            full_repo,
            check_info,
            gh_get_fn=gh_get_fn,
            run_cmd_fn=run_cmd_fn,
            repo_root=repo_root,
        )
        if passed:
            sha_label = head_sha or "main"
            comment = f"already resolved on main at {sha_label}"
            return PremiseCheckResult(
                allowed=False,
                reason="already_resolved_on_main",
                detail=f"premise check '{check_info['check_name']}' already passing on main at {sha_label}",
                comment=comment,
                should_close=False,
                head_sha=sha_label,
                check_name=check_info["check_name"],
            )

    return PremiseCheckResult(allowed=True)


async def log_dispatch_skip(
    entry: PremiseCheckResult,
    repository: str,
    issue_number: int | None,
    *,
    audit_fn: Any = None,
) -> None:
    """Record a dispatch skip in the dispatch audit log."""
    target = f"{repository}#{issue_number}" if issue_number else repository
    record: dict[str, Any] = {
        "event_id": uuid4().hex,
        "action": "dispatch.premise_check",
        "decision": "skipped",
        "target": target,
        "reason": entry.reason,
        "detail": entry.detail,
        "recorded_at": utc_now_iso(),
    }
    if audit_fn:
        await audit_fn(record)
    else:
        try:
            from orchestration_audit import append_orchestration_audit  # noqa: PLC0415

            await append_orchestration_audit(record)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to append dispatch audit skip log: %s", exc)


async def close_item_as_resolved_on_main(
    repository: str,
    issue_number: int,
    comment: str,
    *,
    run_cmd_fn: Any = None,
) -> bool:
    """Post an operator-visible note that a premise check passed on main (#4260: comment only)."""
    full_repo = _normalize_repo(repository)
    comment_cmd = [
        "gh",
        "issue",
        "comment",
        str(issue_number),
        "--repo",
        full_repo,
        "--body",
        comment,
    ]

    try:
        if run_cmd_fn:
            await run_cmd_fn(comment_cmd, timeout=30)
        else:
            from system_utils import run_cmd  # noqa: PLC0415

            await run_cmd(comment_cmd, timeout=30)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to comment on issue %s#%d: %s", full_repo, issue_number, exc)
        return False
