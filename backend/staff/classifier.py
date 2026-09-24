"""Staff run failure classification and remediation hints (SC-A6, Issue #1297).

Maps raw process exits, watchdog signals, stderr/stdout streams, and provider
patterns into actionable failure classifications and deduplicated attention items.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from staff.adapters import _extract_text

if TYPE_CHECKING:
    from staff.store import RunRecord

NO_RESULT_ERROR = "agent exited 0 without a STAFF_RESULT line (it stopped before finishing, e.g. to ask a question)"

ALLOWED_FAILURE_CLASSES = frozenset(
    {
        "auth_expired",
        "cli_missing",
        "provider_error",
        "rate_limited",
        "needs_input",
        "timeout",
        "stalled",
        "lease_blocked",
        "orphaned",
        "workspace_error",
        "unkillable",
        "unknown",
    }
)

LOGIN_COMMANDS: dict[str, str] = {
    "claude": "CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login",
    "codex": "codex login --device-auth",
    "cursor-agent": "cursor-agent login",
    "antigravity": "agy auth login",
    "gemini": "gemini login",
    "ollama": "systemctl --user start ollama",
    "claude-ollama": "systemctl --user start ollama",
}


@dataclass(frozen=True)
class FailureClassification:
    """Structured failure diagnosis with retry semantics and remediation hint."""

    failure_class: str
    retryable: bool
    remediation: str
    error: str = ""
    question: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "retryable": self.retryable,
            "remediation": self.remediation,
            "error": self.error,
            "question": self.question,
        }


def _login_command(provider: str) -> str:
    return LOGIN_COMMANDS.get(provider, f"{provider} login")


def _extract_last_line_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for raw_line in reversed(lines):
        candidate = raw_line
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    extracted = _extract_text(parsed)
                    if extracted.strip():
                        candidate = extracted.strip()
            except Exception:
                pass
        cand_lines = [cl.strip() for cl in candidate.splitlines() if cl.strip()]
        if cand_lines:
            return cand_lines[-1]
    return ""


def classify_run_failure(
    provider: str,
    exit_code: int,
    *,
    output_text: str = "",
    error_message: str = "",
    watchdog_failure_class: str = "",
    watchdog_error: str = "",
    result_line: str = "",
    machine: str = "",
) -> FailureClassification:
    """Classify a failed staff run and provide actionable remediation guidance.

    Pre: provider is non-empty string.
    Post: returned FailureClassification.failure_class is in ALLOWED_FAILURE_CLASSES.
    """
    node = machine or "local node"

    # 1. Watchdog-driven failures
    if watchdog_failure_class == "timeout":
        msg = watchdog_error or "Execution wall-clock timeout exceeded."
        return FailureClassification(
            failure_class="timeout",
            retryable=True,
            remediation=f"Run timed out (timeout: {msg}); will retry if retry policy allows.",
            error=msg,
        )
    if watchdog_failure_class == "stalled":
        msg = watchdog_error or "Idle timeout exceeded (no output produced)."
        return FailureClassification(
            failure_class="stalled",
            retryable=True,
            remediation=f"Run stalled ({msg}); will retry if retry policy allows.",
            error=msg,
        )
    if watchdog_failure_class == "unkillable":
        msg = watchdog_error or "Process group could not be terminated after grace period."
        return FailureClassification(
            failure_class="unkillable",
            retryable=False,
            remediation=f"Process on node {node} could not be terminated; manual check required.",
            error=msg,
        )
    if watchdog_failure_class == "orphaned" or error_message == "orphaned":
        return FailureClassification(
            failure_class="orphaned",
            retryable=False,
            remediation=f"Run orphaned across dashboard restart on node {node}; inspect worktree.",
            error="orphaned across restart",
        )

    # 2. Check for needs_input: exit 0 without STAFF_RESULT and ending in a question
    combined = f"{error_message}\n{output_text}".strip()
    if exit_code == 0 and not result_line:
        last_line = _extract_last_line_text(output_text)
        if last_line.endswith("?"):
            return FailureClassification(
                failure_class="needs_input",
                retryable=False,
                remediation=f"Agent paused asking: {last_line}",
                error=NO_RESULT_ERROR,
                question=last_line,
            )
        # Exited 0 without result and not asking a question
        return FailureClassification(
            failure_class="unknown",
            retryable=False,
            remediation="Process exited 0 without emitting STAFF_RESULT or question.",
            error=NO_RESULT_ERROR,
        )

    lower = combined.lower()

    # 3. CLI Missing
    cli_missing_patterns = (
        "command not found",
        "not recognized as an internal or external command",
        "executable file not found",
        "no such file or directory",
        "filenotfounderror",
    )
    if any(pat in lower for pat in cli_missing_patterns) and (provider in lower or exit_code in (127, 2)):
        return FailureClassification(
            failure_class="cli_missing",
            retryable=False,
            remediation=f"Install `{provider}` on node {node} and ensure it is in PATH.",
            error=f"Executable for {provider} not found on node {node}",
        )

    # 4. Authentication / OAuth Expired
    auth_patterns = (
        "401 oauth access token has expired",
        "401 unauthorized",
        "unauthorized",
        "token expired",
        "authentication expired",
        "session expired",
        "missing organization in id_token claims",
        "please run claude auth login",
        "please login",
        "auth login",
        "not authenticated",
        "auth error",
        "invalid api key",
        "cursor-agent login",
        "authentication failed",
    )
    if any(pat in lower for pat in auth_patterns):
        login_cmd = _login_command(provider)
        return FailureClassification(
            failure_class="auth_expired",
            retryable=False,
            remediation=f"Run `{login_cmd}` on node {node}.",
            error=f"Authentication expired for {provider} on node {node}",
        )

    # 5. Rate limits
    rate_patterns = (
        "429",
        "rate limit",
        "rate_limit_exceeded",
        "too many requests",
        "overloaded_error",
        "overloaded",
        "exceeded your current quota",
    )
    if any(pat in lower for pat in rate_patterns):
        return FailureClassification(
            failure_class="rate_limited",
            retryable=True,
            remediation=f"Provider {provider} rate limit reached; back off before retry.",
            error=f"Rate limit exceeded on {provider}",
        )

    # 6. Lease Blocked / Claims Conflict
    lease_patterns = (
        "lease already held",
        "claim rejected",
        "409 conflict",
        "failed to acquire lease",
        "lease conflict",
    )
    if any(pat in lower for pat in lease_patterns):
        return FailureClassification(
            failure_class="lease_blocked",
            retryable=True,
            remediation="Target work item is currently leased by another agent; await release.",
            error="Lease acquisition blocked",
        )

    # 7. Workspace / Git errors
    workspace_patterns = (
        "already checked out at",
        "fatal: not a git repository",
        "worktree already exists",
        "no space left on device",
        "disk quota exceeded",
    )
    if any(pat in lower for pat in workspace_patterns):
        return FailureClassification(
            failure_class="workspace_error",
            retryable=False,
            remediation=f"Workspace or git failure on node {node}; inspect local git/disk state.",
            error="Workspace error on worker node",
        )

    # 8. Provider / Server Error
    provider_patterns = (
        "500 internal server error",
        "502 bad gateway",
        "503 service unavailable",
        "504 gateway timeout",
        "connection refused",
        "failed to connect to ollama",
        "ollama connection refused",
    )
    if any(pat in lower for pat in provider_patterns) or re.search(r"\b50[0-4]\b", lower):
        detail = output_text.strip()[:150] or error_message or "server or connection error"
        return FailureClassification(
            failure_class="provider_error",
            retryable=True,
            remediation=f"Provider {provider} returned a server or connection error ({detail}); retryable.",
            error=f"Provider service failure ({provider})",
        )

    # 9. Fallback / Unknown
    last_lines = [line for line in output_text.splitlines() if line.strip()][-20:]
    snippet = " | ".join(last_lines) if last_lines else (error_message or f"Exit code {exit_code}")
    if len(snippet) > 300:
        snippet = snippet[-300:]
    return FailureClassification(
        failure_class="unknown",
        retryable=False,
        remediation=f"Unclassified failure: {snippet}",
        error=error_message or f"Exited with code {exit_code}",
    )


def format_attention_items(runs: list[RunRecord]) -> list[dict[str, Any]]:
    """Format and deduplicate attention items for staff summary.

    Auth expiry on any node creates one deduplicated attention item naming the node,
    provider, and the exact sign-in command (SC-A6).
    """
    items: list[dict[str, Any]] = []
    auth_groups: dict[tuple[str, str], list[RunRecord]] = {}

    for r in runs:
        if r.status not in ("failed", "blocked"):
            continue
        failure_cls = getattr(r, "failure_class", "")
        if failure_cls == "auth_expired":
            key = (r.machine, r.provider)
            auth_groups.setdefault(key, []).append(r)
        else:
            items.append(
                {
                    "id": r.id,
                    "role": r.role,
                    "provider": r.provider,
                    "machine": r.machine,
                    "repo": r.repo,
                    "target_ref": r.target_ref,
                    "status": r.status,
                    "error": r.error,
                    "failure_class": failure_cls,
                    "retryable": getattr(r, "retryable", False),
                    "remediation": getattr(r, "remediation", ""),
                }
            )

    # Insert deduplicated auth_expired attention items
    for (node, provider), grouped_runs in auth_groups.items():
        first = grouped_runs[0]
        login_cmd = _login_command(provider)
        items.append(
            {
                "id": f"auth-expired-{node}-{provider}",
                "role": first.role,
                "provider": provider,
                "machine": node,
                "repo": first.repo,
                "target_ref": first.target_ref,
                "status": "failed",
                "failure_class": "auth_expired",
                "retryable": False,
                "error": f"Authentication expired for {provider} on {node}",
                "remediation": f"Run `{login_cmd}` on node {node}.",
                "affected_runs": [run.id for run in grouped_runs],
            }
        )

    return items


def classify_execution_result(
    *,
    cancelled: bool,
    rc: int,
    result_line: str,
    provider: str,
    transcript_path: Path | None,
    watchdog_failure_class: str = "",
    watchdog_error: str = "",
    machine: str = "",
) -> tuple[str, str, bool, str, str]:
    """Determine (status, failure_class, retryable, remediation, error) for a finished run."""
    if cancelled:
        return "cancelled", "", False, "", ""
    if rc == 0 and result_line:
        return "succeeded", "", False, "", ""

    output_text = ""
    if transcript_path and transcript_path.exists():
        try:
            output_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    diag = classify_run_failure(
        provider=provider,
        exit_code=rc,
        output_text=output_text,
        error_message=watchdog_error,
        watchdog_failure_class=watchdog_failure_class,
        watchdog_error=watchdog_error,
        result_line=result_line,
        machine=machine,
    )
    err = diag.error or (watchdog_error if watchdog_failure_class else "")
    if rc == 0 and not result_line and not err:
        err = "An unattended agent that stops to ask a question exits 0 without finishing."
    return "failed", diag.failure_class, diag.retryable, diag.remediation, err
