"""Event-driven CI-fix dispatch engine (RD-1 / Issue #1846).

Dispatches fresh, small, capped sessions on failing CI for open PRs with auto-merge armed:
- Truncates log tail to <= 200 lines.
- Extracts failing test names.
- Enforces strict concurrency lock (1 CI-fix session per PR at a time); a lock ends when
  its staff run ends or after ``CI_FIX_LOCK_TTL_SECONDS`` (#1881).
- Recovers the PR of a merge-queue run from its ``gh-readonly-queue/*`` head ref (#1879).
- Routes lint/format to cheapest provider (< $0.50), tests/logic to tier:cli (agy, else Sonnet).
- Escalates to tier:strong after max_same_failure_attempts (default 3).
- Records audit entries for cost, attempt, model, and provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ci_fix_locks import GLOBAL_CI_FIX_LOCK_MGR, CIFixLockManager
from dispatch_effort import resolve_effort
from time_utils import utc_now_iso

logger = logging.getLogger("dashboard.ci_fix_dispatch")

DEFAULT_AUDIT_PATH = (
    Path(os.environ.get("RUNNER_DASHBOARD_DATA_DIR", Path.home() / ".local" / "share" / "runner-dashboard"))
    / "ci_fix_audit.json"
)

# #1879: merge-queue runs execute on ``gh-readonly-queue/<base>/pr-<N>-<sha>`` with an empty
# ``pull_requests``; ``<base>`` may itself contain slashes.
QUEUE_REF_PATTERN = re.compile(r"^gh-readonly-queue/(?P<base>.+)/pr-(?P<number>\d+)-(?P<sha>[0-9a-f]{7,40})$")

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


def pr_number_from_queue_ref(ref: str) -> int | None:
    """PR number of a merge-queue head ref ``gh-readonly-queue/<base>/pr-<N>-<sha>``, else None."""
    match = QUEUE_REF_PATTERN.match(ref or "")
    return int(match.group("number")) if match else None


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


# Route models (#1880). tier:cli prefers agy with Gemini 3.8 Flash, then Claude Sonnet
# (Repository_Management agent-tier rule); the agy model matches conductor/tiers.py.
STRONG_MODEL = "claude-opus-5-5"
CLI_CLAUDE_MODEL = "claude-sonnet-5-5"
CHEAP_CODEX_MODEL = "gpt-6-luna"
AGY_MODEL = "gemini-3.8-flash-high"


def _agy_runs_unattended() -> bool:
    """Whether agy can take an unattended CI fix: the staff adapter's own flag (#1880)."""
    from staff.adapters import ADAPTERS  # noqa: PLC0415

    adapter = ADAPTERS.get("antigravity")
    return bool(adapter is not None and adapter.unattended)


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
            model=STRONG_MODEL,
            cost_budget=5.00,
            escalated=True,
            reason=f"Escalated to strong tier after {attempt_number} consecutive attempts",
            effort=resolve_effort("ci_fix:escalated"),
        )

    if failure_type == "lint":
        return CIFixRoute(
            provider="codex_cli",
            tier="cheap",
            model=CHEAP_CODEX_MODEL,
            cost_budget=0.40,
            escalated=False,
            reason="Routed to cheap tier for narrow lint/format fix",
            effort=resolve_effort("ci_fix:lint"),
        )

    if _agy_runs_unattended():
        return CIFixRoute(
            provider="antigravity",
            tier="cli",
            model=AGY_MODEL,
            cost_budget=1.50,
            escalated=False,
            reason="Routed to standard CLI tier (agy, Gemini 3.8 Flash) for test/logic fix",
            effort=resolve_effort(f"ci_fix:{failure_type}"),
        )

    return CIFixRoute(
        provider="claude_code_cli",
        tier="cli",
        model=CLI_CLAUDE_MODEL,
        cost_budget=1.50,
        escalated=False,
        reason="Routed to standard CLI tier for test/logic fix (agy skipped: it cannot run unattended)",
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
        queued = pr_number_from_queue_ref(str(event_payload.get("head_branch") or event_payload.get("branch") or ""))
        # #1879: a merge-queue entry is never a draft and was queued to merge.
        prs = [{"number": queued, "is_draft": False, "auto_merge_armed": True}] if queued else []
    if not prs:
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
    cost_budget: float,
    audit_file: Path | None = None,
    staff_run_id: str = "",
    effort: str = "",
    failure_signature: str = "",
) -> None:
    """Record CI-fix dispatch telemetry for budget and retry accounting.

    ``cost_budget`` is the route's cap, not the spend (#1881); the spend is on the staff run
    ``staff_run_id`` names.
    """
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
        "cost_budget": round(cost_budget, 4),
        "staff_run_id": staff_run_id,
        "effort": effort,
        "failure_signature": failure_signature,
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


def failure_signature(workflow_name: str, failing_tests: list[str], failure_type: str) -> str:
    """Stable identity of one failure: the workflow plus its failing tests (else its type)."""
    detail = ",".join(sorted(set(failing_tests))) or failure_type
    return hashlib.sha256(f"{workflow_name}|{detail}".encode()).hexdigest()[:16]


def count_consecutive_attempts(repo: str, pr_number: int, signature: str, audit_file: Path | None = None) -> int:
    """Length of this PR's current streak of dispatches for the same failure (escalation input).

    Walks the PR's audit rows newest-first and stops at the first row with another
    signature, so a new or different failure starts a fresh streak (#1887 review).
    """
    target_path = audit_file or DEFAULT_AUDIT_PATH
    try:
        entries = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(entries, list):
        return 0
    repo_key = repo.strip().lower()
    streak = 0
    for e in reversed(entries):
        if (
            not isinstance(e, dict)
            or str(e.get("repository", "")).lower() != repo_key
            or e.get("pr_number") != pr_number
        ):
            continue
        if e.get("failure_signature") != signature:
            break
        streak += 1
    return streak
