"""Pre-dispatch premise check for task, issue, and follow-up chip dispatches.

Part of Repository_Management#1889 / Issue #1847 (RD-2).

Before dispatching any agent session from an issue, a follow-up chip or `spawn_task`:
1. If the task names a failing check or command, re-run it once against current
   `main` (one REST call or a local command on the node). If it passes, comment
   "already resolved on main at <sha>", close the item, and don't dispatch.
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

# ─── Audited No-Op Prompts Replay Register ────────────────────────────────────

AUDITED_NO_OP_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "lod-baseline",
        "repository": "D-sorganization/Runner_Dashboard",
        "issue": 1720,
        "prompt": "Fix Law of Demeter violations: check_law_of_demeter baseline on branch",
        "description": "the LoD baseline had already been fixed on the branch",
    },
    {
        "id": "conflict-marker",
        "repository": "D-sorganization/Runner_Dashboard",
        "issue": 1740,
        "prompt": "Remove stray conflict marker <<<<<<< in docs/HANDOFF.md",
        "description": "the stray conflict marker had already been removed by #1739, 39 minutes earlier",
    },
    {
        "id": "local-only-runner-guard",
        "repository": "D-sorganization/AffineDrift",
        "issue": 4200,
        "prompt": "Fix failing workflow local-only-runner-guard.yml on AffineDrift",
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


# ─── Check & Command Extraction ───────────────────────────────────────────────

_CHECK_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"local-only-runner-guard(?:\.ya?ml)?", re.I), "local-only-runner-guard", "workflow"),
    (re.compile(r"check_law_of_demeter(?:\.py)?|law of demeter|lod baseline", re.I), "check_law_of_demeter", "script"),
    (re.compile(r"<<<<<<<|conflict marker", re.I), "check_conflict_markers", "conflict_marker"),
    (re.compile(r"check_dry_duplication_gate(?:\.py)?", re.I), "check_dry_duplication_gate", "script"),
    (re.compile(r"check_architecture_budget(?:\.py)?", re.I), "check_architecture_budget", "script"),
    (re.compile(r"([\w.-]+\.(?:ya?ml))", re.I), r"\1", "workflow"),
]

_COMMAND_PATTERN = re.compile(
    r"(?:^|\s)(pytest\s+[\w/.:-]+|python3?\s+[\w/.:-]+\.py|ruff\s+[\w/.:-]+|git\s+grep\s+['\"][^'\"]+['\"])",
    re.I,
)


def extract_failing_check_premise(text: str) -> dict[str, Any] | None:
    """Detect whether a prompt or issue body specifies a failing check or command."""
    if not text:
        return None

    # Check for known check patterns / workflows
    for pat, name_template, check_type in _CHECK_PATTERNS:
        match = pat.search(text)
        if match:
            check_name = match.expand(name_template) if "\\" in name_template else name_template
            command = f"python3 scripts/ci/{check_name}.py" if check_type == "script" else ""
            if check_type == "conflict_marker":
                command = "git grep -n '<<<<<<<'"
            return {
                "check_name": check_name,
                "check_type": check_type,
                "command": command,
            }

    # Check for shell command invocation
    cmd_match = _COMMAND_PATTERN.search(text)
    if cmd_match:
        cmd = cmd_match.group(1).strip()
        return {
            "check_name": cmd.split()[0],
            "check_type": "command",
            "command": cmd,
        }

    return None


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
    ref_patterns = [
        re.compile(rf"(?:closes|fixes|refs|resolves)[^\d]*#{issue_ref}\b", re.I),
        re.compile(rf"#{issue_ref}\b"),
        re.compile(rf"issue-?{issue_ref}\b", re.I),
    ]

    for pr in pulls:
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        head_ref = str((pr.get("head") or {}).get("ref") or "")
        haystack = f"{title}\n{body}\n{head_ref}"
        if any(p.search(haystack) for p in ref_patterns):
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
) -> tuple[bool, str]:
    """Return True if an active claim:* lease or label exists for the issue."""
    full_repo = _normalize_repo(repository)
    endpoint = f"/repos/{full_repo}/issues/{issue_number}"

    issue: dict[str, Any] = {}
    if gh_get_fn:
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
    for cr in check_runs:
        cr_name = str(cr.get("name") or "").lower()
        if not head_sha:
            head_sha = str(cr.get("head_sha") or "")[:7]
        target_name = check_name.lower().replace(".yml", "").replace(".yaml", "")
        if target_name in cr_name or cr_name in target_name:
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

    # 1. Try REST API check-run query on main
    passed, head_sha = await _check_runs_on_main(full_repo, check_name, gh_get_fn)
    if passed is not None:
        return passed, head_sha or "main"

    # 2. Try running local command if run_cmd_fn provided
    command = check_info.get("command")
    if command and run_cmd_fn:
        cmd_args = command.split() if isinstance(command, str) else list(command)
        try:
            cwd = repo_root or Path.cwd()
            code, _stdout, _stderr = await run_cmd_fn(cmd_args, timeout=30, cwd=cwd)
            return code == 0, head_sha or "main"
        except Exception as exc:  # noqa: BLE001
            log.warning("Local check command %s failed to run: %s", command, exc)

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
) -> PremiseCheckResult:
    """Evaluate whether a task dispatch premise is valid or should be skipped.

    Enforces:
    - Skip if active claim:* lease or label exists.
    - Skip if an open PR references the issue.
    - Skip if named failing check/command already passes on current main.
    """
    full_repo = _normalize_repo(repository)

    # 1. Active claim check
    if issue_number:
        has_claim, claim_detail = await check_issue_has_active_claim(
            full_repo,
            issue_number,
            gh_get_fn=gh_get_fn,
            lease_mgr=lease_mgr,
        )
        if has_claim:
            return PremiseCheckResult(
                allowed=False,
                reason="active_claim_exists",
                detail=claim_detail,
            )

    # 2. Open PR reference check
    if issue_number:
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

    # 3. Failing check or command premise re-check against main
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
                should_close=True,
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
    """Comment on and close an issue that is already resolved on main."""
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
    close_cmd = [
        "gh",
        "issue",
        "close",
        str(issue_number),
        "--repo",
        full_repo,
        "--reason",
        "completed",
    ]

    try:
        if run_cmd_fn:
            await run_cmd_fn(comment_cmd, timeout=30)
            await run_cmd_fn(close_cmd, timeout=30)
        else:
            from system_utils import run_cmd  # noqa: PLC0415

            await run_cmd(comment_cmd, timeout=30)
            await run_cmd(close_cmd, timeout=30)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to comment and close issue %s#%d: %s", full_repo, issue_number, exc)
        return False
