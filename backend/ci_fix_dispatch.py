"""Event-driven CI-fix dispatch engine (RD-1 / Issue #1846).

Dispatches fresh, small, capped sessions on failing CI for open PRs with auto-merge armed:
- Truncates log tail to <= 200 lines.
- Extracts failing test names.
- Enforces strict concurrency lock (1 CI-fix session per PR at a time).
- Routes lint/format to cheapest provider (< $0.50), tests/logic to tier:cli.
- Escalates to tier:strong after max_same_failure_attempts (default 3).
- Records audit entries for cost, attempt, model, and provider.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dispatch_effort import resolve_effort
from time_utils import utc_now_iso

logger = logging.getLogger("dashboard.ci_fix_dispatch")

DEFAULT_AUDIT_PATH = (
    Path(os.environ.get("RUNNER_DASHBOARD_DATA_DIR", Path.home() / ".local" / "share" / "runner-dashboard"))
    / "ci_fix_audit.json"
)

TEST_FAILURE_PATTERNS = (
    re.compile(r"^\s*(?:FAILED|FAIL)\s+([^\s:]+(?:::[^\s]+)?(?:\s+-\s+.*)?)$", re.MULTILINE),
    re.compile(r"^\s*ERROR\s+([^\s:]+(?:::[^\s]+)?)$", re.MULTILINE),
)


@dataclass(frozen=True, slots=True)
class CIFixTriggerDecision:
    """Decision outcome of CI-fix event evaluation."""

    eligible: bool
    reason: str
    pr_number: int | None = None
    workflow_name: str = ""
    run_id: int | None = None
    failure_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CIFixRoute:
    """Routing destination and budget for CI fix."""

    provider: str
    tier: str
    model: str
    cost_budget: float
    escalated: bool = False
    reason: str = ""
    # USE-1 (#1865): reasoning effort for the launched session.
    effort: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CIFixLockManager:
    """Thread-safe concurrency lock manager for CI-fix sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_locks: dict[tuple[str, int], dict[str, Any]] = {}

    def acquire(self, repo: str, pr_number: int, session_id: str = "") -> bool:
        """Acquire single-session concurrency lock for a PR."""
        key = (repo.strip().lower(), pr_number)
        with self._lock:
            if key in self._active_locks:
                return False
            self._active_locks[key] = {
                "repo": repo,
                "pr_number": pr_number,
                "session_id": session_id,
                "acquired_at": utc_now_iso(),
            }
            return True

    def release(self, repo: str, pr_number: int) -> bool:
        """Release concurrency lock for a PR."""
        key = (repo.strip().lower(), pr_number)
        with self._lock:
            return self._active_locks.pop(key, None) is not None

    def is_locked(self, repo: str, pr_number: int) -> bool:
        """Check if PR currently has an active CI-fix session."""
        key = (repo.strip().lower(), pr_number)
        with self._lock:
            return key in self._active_locks

    def get_lock_info(self, repo: str, pr_number: int) -> dict[str, Any] | None:
        """Return lock metadata if locked."""
        key = (repo.strip().lower(), pr_number)
        with self._lock:
            return self._active_locks.get(key)

    def list_locks(self) -> list[dict[str, Any]]:
        """Return snapshot of all active locks."""
        with self._lock:
            return list(self._active_locks.values())


# Global singleton instance for application lifetime
GLOBAL_CI_FIX_LOCK_MGR = CIFixLockManager()


def truncate_log_tail(log_text: str, max_lines: int = 200) -> str:
    """Return the last max_lines lines from a log."""
    if not log_text:
        return ""
    lines = log_text.splitlines()
    if len(lines) <= max_lines:
        return log_text
    return "\n".join(lines[-max_lines:])


def extract_failing_test_names(log_text: str) -> list[str]:
    """Parse out explicit failed test identifiers from log output."""
    if not log_text:
        return []
    names: list[str] = []
    for pat in TEST_FAILURE_PATTERNS:
        for match in pat.finditer(log_text):
            raw = match.group(1).split(" - ")[0].strip()
            if raw and raw not in names:
                names.append(raw)
    return names


def classify_failure_type(workflow_name: str, log_excerpt: str) -> str:
    """Classify the failure category from workflow name and log cues."""
    wf_lower = workflow_name.lower()
    log_lower = log_excerpt.lower()
    if any(k in wf_lower for k in ("lint", "format", "style", "ruff", "black")):
        return "lint"
    if "conflict" in log_lower or "merge conflict" in log_lower:
        return "conflict"
    if any(k in wf_lower for k in ("security", "audit", "secret")):
        return "security"
    if any(k in wf_lower for k in ("spec", "contract")):
        return "spec"
    return "test"


def route_ci_fix(
    failure_type: str,
    attempt_number: int = 1,
    max_attempts: int = 3,
) -> CIFixRoute:
    """Route failure to appropriate provider tier based on type and attempts."""
    if attempt_number >= max_attempts:
        return CIFixRoute(
            provider="claude_code_cli",
            tier="strong",
            model="claude-3-7-opus",
            cost_budget=5.00,
            escalated=True,
            reason=f"Escalated to strong tier after {attempt_number} consecutive attempts",
            effort=resolve_effort("ci_fix:escalated"),
        )

    if failure_type == "lint":
        return CIFixRoute(
            provider="codex_cli",
            tier="cheap",
            model="gpt-5-codex",
            cost_budget=0.40,
            escalated=False,
            reason="Routed to cheap tier for narrow lint/format fix",
            effort=resolve_effort("ci_fix:lint"),
        )

    return CIFixRoute(
        provider="claude_code_cli",
        tier="cli",
        model="claude-3-5-sonnet",
        cost_budget=1.50,
        escalated=False,
        reason="Routed to standard CLI tier for test/logic fix",
        effort=resolve_effort(f"ci_fix:{failure_type}"),
    )


def build_ci_fix_prompt(
    repo: str,
    pr_number: int,
    branch: str,
    log_tail: str,
    failing_tests: list[str],
    pr_diff: str,
    conflicting_files: list[str] | None = None,
) -> str:
    """Build a concise, fresh prompt strictly bounded to failure details."""
    parts = [
        f"CI-Fix Task: Repository {repo} PR #{pr_number} (Branch: {branch})",
        "",
        "Instructions:",
        "1. Fix the failing CI checks with the smallest possible change set.",
        "2. Do not perform unrelated refactors.",
        "3. Run `python -m scripts.pre_pr` locally to verify all CI gates pass.",
        "4. Push once and end the session immediately.",
        "",
    ]

    if conflicting_files:
        parts.extend(
            [
                "Merge conflict detected in the following files:",
                *(f"  - {f}" for f in conflicting_files),
                "",
                "Resolve the conflict markers cleanly and verify tests pass.",
                "",
            ]
        )

    if failing_tests:
        parts.extend(
            [
                "Failing Tests:",
                *(f"  - {t}" for t in failing_tests),
                "",
            ]
        )

    if log_tail.strip():
        parts.extend(
            [
                "Failing Job Log Tail (≤ 200 lines):",
                "```",
                truncate_log_tail(log_tail, max_lines=200),
                "```",
                "",
            ]
        )

    if pr_diff.strip():
        parts.extend(
            [
                "Active PR Diff:",
                "```diff",
                pr_diff[:8000],
                "```",
                "",
            ]
        )

    return "\n".join(parts)


def evaluate_ci_fix_trigger(
    event_payload: dict[str, Any],
    lock_manager: CIFixLockManager | None = None,
) -> CIFixTriggerDecision:
    """Evaluate whether an incoming webhook or workflow event triggers CI-fix."""
    conclusion = str(event_payload.get("conclusion", "")).lower()
    workflow_name = str(event_payload.get("workflow_name", ""))
    repo = str(event_payload.get("repository", ""))
    run_id = event_payload.get("run_id")

    if conclusion not in ("failure", "timed_out", "cancelled"):
        return CIFixTriggerDecision(
            eligible=False,
            reason=f"Run conclusion '{conclusion}' is not failed",
            workflow_name=workflow_name,
            run_id=run_id,
        )

    prs = event_payload.get("pull_requests", [])
    if not prs or not isinstance(prs, list):
        return CIFixTriggerDecision(
            eligible=False,
            reason="Event is not associated with an open pull request",
            workflow_name=workflow_name,
            run_id=run_id,
        )

    pr = prs[0]
    pr_num = pr.get("number")
    is_draft = pr.get("is_draft", False)
    auto_merge_armed = pr.get("auto_merge_armed", False)

    if is_draft:
        return CIFixTriggerDecision(
            eligible=False,
            reason="Auto-fix is disabled on draft PRs (RD-0)",
            pr_number=pr_num,
            workflow_name=workflow_name,
            run_id=run_id,
        )

    if not auto_merge_armed:
        return CIFixTriggerDecision(
            eligible=False,
            reason="PR does not have auto-merge armed",
            pr_number=pr_num,
            workflow_name=workflow_name,
            run_id=run_id,
        )

    locks = lock_manager or GLOBAL_CI_FIX_LOCK_MGR
    if pr_num is not None and locks.is_locked(repo, pr_num):
        return CIFixTriggerDecision(
            eligible=False,
            reason=f"A CI-fix session is already active for {repo}#{pr_num}",
            pr_number=pr_num,
            workflow_name=workflow_name,
            run_id=run_id,
        )

    failure_type = classify_failure_type(workflow_name, "")
    return CIFixTriggerDecision(
        eligible=True,
        reason="Eligible for event-driven CI-fix dispatch",
        pr_number=pr_num,
        workflow_name=workflow_name,
        run_id=run_id,
        failure_type=failure_type,
    )


def record_ci_fix_audit(
    repo: str,
    pr_number: int,
    workflow_name: str,
    run_id: int | None,
    failure_type: str,
    provider: str,
    model: str,
    attempt_number: int,
    cost_estimate: float,
    audit_file: Path | None = None,
) -> None:
    """Record CI-fix dispatch telemetry for cost and retry accounting."""
    target_path = audit_file or DEFAULT_AUDIT_PATH
    entry = {
        "timestamp": utc_now_iso(),
        "repository": repo,
        "pr_number": pr_number,
        "workflow_name": workflow_name,
        "run_id": run_id,
        "failure_type": failure_type,
        "provider": provider,
        "model": model,
        "attempt_number": attempt_number,
        "cost_estimate": round(cost_estimate, 4),
    }

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        if target_path.is_file():
            try:
                entries = json.loads(target_path.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = []
            except (json.JSONDecodeError, OSError):
                entries = []
        entries.append(entry)
        target_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write CI-fix audit log: %s", exc)
