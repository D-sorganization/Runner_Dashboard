"""Remediation planning, prompt generation, and workflow health inspection.

Extracted from agent_remediation.py (issue #361).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .policy import (
    AttemptRecord,
    FailureContext,
    PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION,
    RemediationPolicy,
    WorkflowTypeRule,
    _attempts_for_fingerprint,
    _attempts_for_provider,
    build_failure_fingerprint,
    classify_workflow_type,
)
from .providers import PROVIDERS, ProviderAvailability


@dataclass(frozen=True, slots=True)
class DispatchDecision:
    accepted: bool
    reason: str
    fingerprint: str
    provider_id: str | None = None
    prompt_preview: str = ""
    suggested_workflow: str | None = None
    attempt_count: int = 0
    remaining_attempts: int = 0
    workflow_type: str = "unknown"
    workflow_label: str = "Unclassified"
    dispatch_mode: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkflowHealthEntry:
    workflow_file: str
    workflow_name: str
    exists: bool
    manual_dispatch: bool
    scheduled: bool
    workflow_run_trigger: bool
    trigger_type: str = "dormant"
    issues: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = list(self.issues)
        return data


@dataclass(frozen=True, slots=True)
class WorkflowHealthReport:
    generated_at: str
    control_tower_summary: str
    workflows: tuple[WorkflowHealthEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "control_tower_summary": self.control_tower_summary,
            "workflows": [item.to_dict() for item in self.workflows],
        }


def sanitize_for_prompt(text: str, max_length: int = 2000) -> str:
    """Sanitize user-controlled text before inserting into LLM prompts."""
    if not isinstance(text, str):
        text = str(text)
    text = text[:max_length]
    return f"[START_UNTRUSTED_CONTENT]\n{text}\n[END_UNTRUSTED_CONTENT]"


def provider_prompt(provider_id: str, context: FailureContext) -> str:
    raw_summary = context.failure_reason.strip() or "No concise failure summary was provided."
    raw_log = context.log_excerpt.strip() or "(no log excerpt provided)"
    summary = sanitize_for_prompt(raw_summary)
    log_excerpt = sanitize_for_prompt(raw_log)
    branch_line = f"Repository: {context.repository}\nBranch: {context.branch}\nWorkflow: {context.workflow_name}"
    repair_goal = (
        "Fix the failing CI with the smallest safe change set. Reproduce or reason "
        "from the failure, update tests only when the product behavior is clearly wrong "
        "or underspecified, and avoid unrelated refactors."
    )
    system_note = PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION
    if provider_id == "jules_api":
        return (
            f"{system_note}\n\n"
            f"{branch_line}\nRun ID: {context.run_id or 'unknown'}\n\n"
            f"Failure summary:\n{summary}\n\n"
            f"Failed log excerpt:\n{log_excerpt}\n\n"
            f"{repair_goal}\n"
            "Create a reviewable pull request when ready."
        )
    if provider_id == "codex_cli":
        return (
            f"{system_note}\n\n"
            f"{branch_line}\nRun ID: {context.run_id or 'unknown'}\n\n"
            f"Failure summary:\n{summary}\n\n"
            f"Failed log excerpt:\n{log_excerpt}\n\n"
            f"{repair_goal}\n"
            "Edit the repository directly, run the most relevant local validation you can, "
            "and leave the working tree ready for commit."
        )
    if provider_id == "claude_code_cli":
        return (
            f"{system_note}\n\n"
            f"{branch_line}\nRun ID: {context.run_id or 'unknown'}\n\n"
            f"Failure summary:\n{summary}\n\n"
            f"Failed log excerpt:\n{log_excerpt}\n\n"
            f"{repair_goal}\n"
            "Work inside this checkout, make the minimal code change that addresses the failure, "
            "and verify the narrowest relevant test target."
        )
    if provider_id == "gemini_cli":
        return (
            f"{system_note}\n\n"
            f"{branch_line}\nRun ID: {context.run_id or 'unknown'}\n\n"
            f"Failure summary:\n{summary}\n\n"
            f"Failed log excerpt:\n{log_excerpt}\n\n"
            f"{repair_goal}\n"
            "Analyze the failure, apply the fix to the local codebase, and verify the result."
        )
    return (
        f"{system_note}\n\n"
        f"{branch_line}\nRun ID: {context.run_id or 'unknown'}\n\n"
        f"Failure summary:\n{summary}\n\n"
        f"Failed log excerpt:\n{log_excerpt}\n\n"
        "Analyze this failure and recommend a safe remediation path."
    )


def plan_dispatch(
    context: FailureContext,
    *,
    policy: RemediationPolicy,
    availability: dict[str, ProviderAvailability],
    attempts: list[AttemptRecord],
    provider_override: str | None = None,
    dispatch_origin: str = "manual",
) -> DispatchDecision:
    fingerprint = build_failure_fingerprint(context)
    workflow_rule = classify_workflow_type(context, policy)
    recent_attempts = _attempts_for_fingerprint(
        fingerprint,
        attempts,
        window_hours=policy.attempt_window_hours,
    )
    attempt_count = len(recent_attempts)
    remaining_attempts = max(0, policy.max_same_failure_attempts - attempt_count)

    if dispatch_origin == "automatic" and not policy.auto_dispatch_on_failure:
        return DispatchDecision(
            accepted=False,
            reason="Automatic CI remediation is disabled by policy.",
            fingerprint=fingerprint,
            attempt_count=attempt_count,
            remaining_attempts=remaining_attempts,
            workflow_type=workflow_rule.workflow_type,
            workflow_label=workflow_rule.label,
            dispatch_mode=workflow_rule.dispatch_mode,
        )
    if policy.require_non_protected_branch and context.protected_branch:
        return DispatchDecision(
            accepted=False,
            reason="Protected branches require a PR-producing remediation path instead of direct branch edits.",
            fingerprint=fingerprint,
            attempt_count=attempt_count,
            remaining_attempts=remaining_attempts,
            workflow_type=workflow_rule.workflow_type,
            workflow_label=workflow_rule.label,
            dispatch_mode=workflow_rule.dispatch_mode,
        )
    if policy.require_failure_summary and not (context.failure_reason.strip() or context.log_excerpt.strip()):
        return DispatchDecision(
            accepted=False,
            reason="A failure summary or failed-log excerpt is required before dispatch.",
            fingerprint=fingerprint,
            attempt_count=attempt_count,
            remaining_attempts=remaining_attempts,
            workflow_type=workflow_rule.workflow_type,
            workflow_label=workflow_rule.label,
            dispatch_mode=workflow_rule.dispatch_mode,
        )

    if dispatch_origin == "automatic" and workflow_rule.dispatch_mode != "auto":
        return DispatchDecision(
            accepted=False,
            reason=(f"{workflow_rule.label} failures require manual review before agent dispatch."),
            fingerprint=fingerprint,
            attempt_count=attempt_count,
            remaining_attempts=remaining_attempts,
            workflow_type=workflow_rule.workflow_type,
            workflow_label=workflow_rule.label,
            dispatch_mode=workflow_rule.dispatch_mode,
        )

    candidate_ids: tuple[str, ...]
    if provider_override:
        candidate_ids = (provider_override,)
    else:
        preferred = [workflow_rule.provider_id] if workflow_rule.provider_id else []
        fallback_chain = list(workflow_rule.fallback_providers) if workflow_rule.fallback_providers else []
        remaining_order = [p for p in policy.provider_order if p not in preferred and p not in fallback_chain]
        candidate_ids = tuple(dict.fromkeys(preferred + fallback_chain + remaining_order).keys())

    selected_provider: str | None = None
    exhausted_providers: list[str] = []
    for provider_id in candidate_ids:
        if not provider_id:
            continue
        if provider_id not in policy.enabled_providers:
            continue
        provider = PROVIDERS.get(provider_id)
        provider_status = availability.get(provider_id)
        if provider is None or provider_status is None or not provider_status.available:
            continue

        provider_attempts = _attempts_for_provider(
            fingerprint, provider_id, attempts, window_hours=policy.attempt_window_hours
        )
        provider_attempt_count = len(provider_attempts)
        if provider_attempt_count >= policy.max_same_failure_attempts:
            exhausted_providers.append(f"{provider.label} ({provider_attempt_count} attempts)")
            continue

        selected_provider = provider_id
        break

    if selected_provider:
        provider = PROVIDERS[selected_provider]
        provider_attempts = _attempts_for_provider(
            fingerprint,
            selected_provider,
            attempts,
            window_hours=policy.attempt_window_hours,
        )
        provider_attempt_count = len(provider_attempts)
        return DispatchDecision(
            accepted=True,
            reason=f"Dispatch is allowed via {provider.label}.",
            fingerprint=fingerprint,
            provider_id=selected_provider,
            prompt_preview=provider_prompt(selected_provider, context),
            suggested_workflow=".github/workflows/Agent-CI-Remediation.yml",
            attempt_count=provider_attempt_count,
            remaining_attempts=max(0, policy.max_same_failure_attempts - provider_attempt_count),
            workflow_type=workflow_rule.workflow_type,
            workflow_label=workflow_rule.label,
            dispatch_mode=workflow_rule.dispatch_mode,
        )

    if exhausted_providers:
        return DispatchDecision(
            accepted=False,
            reason=(
                "Loop guard blocked dispatch because all candidate providers have reached "
                f"their attempt limit: {', '.join(exhausted_providers)}."
            ),
            fingerprint=fingerprint,
            attempt_count=attempt_count,
            remaining_attempts=0,
            workflow_type=workflow_rule.workflow_type,
            workflow_label=workflow_rule.label,
            dispatch_mode=workflow_rule.dispatch_mode,
        )

    return DispatchDecision(
        accepted=False,
        reason="No enabled remediation provider is currently available on this host.",
        fingerprint=fingerprint,
        attempt_count=attempt_count,
        remaining_attempts=remaining_attempts,
        workflow_type=workflow_rule.workflow_type,
        workflow_label=workflow_rule.label,
        dispatch_mode=workflow_rule.dispatch_mode,
    )


from .policy import LEGACY_WORKFLOW_PATTERNS  # noqa: E402


def inspect_jules_workflows(repo_root: Path) -> WorkflowHealthReport:
    from time_utils import utc_now_iso

    workflows_dir = repo_root / ".github" / "workflows"
    entries: list[WorkflowHealthEntry] = []
    expected = (
        "Jules-Control-Tower.yml",
        "Jules-Auto-Repair.yml",
        "Jules-Issue-Triage.yml",
        "Jules-Issue-Resolver.yml",
        "Jules-Dispatch.yml",
    )
    control_tower_issues: list[str] = []
    for filename in expected:
        path = workflows_dir / filename
        if not path.exists():
            entries.append(
                WorkflowHealthEntry(
                    workflow_file=filename,
                    workflow_name=filename.removesuffix(".yml"),
                    exists=False,
                    manual_dispatch=False,
                    scheduled=False,
                    workflow_run_trigger=False,
                    trigger_type="dormant",
                    issues=("Workflow file is missing.",),
                )
            )
            continue
        raw = path.read_text(encoding="utf-8")
        issues: list[str] = []
        for needle, message in LEGACY_WORKFLOW_PATTERNS:
            if needle in raw:
                issues.append(message)
        manual_dispatch = "workflow_dispatch:" in raw
        scheduled = re.search(r"^\s*schedule:\s*$", raw, re.MULTILINE) is not None
        workflow_run_trigger = "workflow_run:" in raw
        if manual_dispatch:
            trigger_type = "manual"
        elif scheduled:
            trigger_type = "scheduled"
        elif workflow_run_trigger:
            trigger_type = "workflow_run"
        else:
            trigger_type = "dormant"
        workflow_name_match = re.search(r"^name:\s*(.+)$", raw, re.MULTILINE)
        workflow_name = (
            workflow_name_match.group(1).strip().strip('"').strip("'")
            if workflow_name_match
            else filename.removesuffix(".yml")
        )
        if filename == "Jules-Control-Tower.yml":
            cron_count = len(re.findall(r"-\s+cron:\s+", raw))
            if cron_count <= 1:
                control_tower_issues.append(
                    "Control Tower currently has only one scheduled cron entry, so low Jules activity"
                    " is expected unless manual or workflow_run triggers fire."
                )
            if 'target = "auto-repair"' in raw and "call-repair:" in raw:
                control_tower_issues.append(
                    "Control Tower still routes CI failures through the legacy repair worker"
                    " unless explicitly migrated."
                )
        entries.append(
            WorkflowHealthEntry(
                workflow_file=filename,
                workflow_name=workflow_name,
                exists=True,
                manual_dispatch=manual_dispatch,
                scheduled=scheduled,
                workflow_run_trigger=workflow_run_trigger,
                trigger_type=trigger_type,
                issues=tuple(issues),
            )
        )
    if not control_tower_issues:
        control_tower_summary = "No obvious local Control Tower health issue was detected."
    else:
        control_tower_summary = " ".join(control_tower_issues)
    return WorkflowHealthReport(
        generated_at=utc_now_iso(),
        control_tower_summary=control_tower_summary,
        workflows=tuple(entries),
    )
