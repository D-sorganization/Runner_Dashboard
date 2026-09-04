"""Remediation planning, prompt generation, and workflow health inspection.

Extracted from agent_remediation.py (issue #361).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .policy import (
    PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION,
    AttemptRecord,
    FailureContext,
    RemediationPolicy,
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
    summary: str
    workflows: tuple[WorkflowHealthEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        # ``control_tower_summary`` is the pre-RM#1483 key name, still read by
        # the Remediation tab. Two-step schema change: ship ``summary``
        # alongside it now, drop the alias in the next release.
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "control_tower_summary": self.summary,
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


from .policy import LEGACY_WORKFLOW_PATTERNS, RETIRED_WORKFLOW_PATTERNS  # noqa: E402

#: Filename prefix that marks a workflow as part of the agent remediation
#: surface. The probe discovers these from disk rather than asserting a
#: hardcoded inventory: the Jules suite was retired fleet-wide by
#: D-sorganization/Repository_Management#1483 (program RM#1505) and the old
#: hardcoded tuple then reported every retired file as missing. Discovery keeps
#: the probe correct across the next retirement without a code change.
AGENT_WORKFLOW_PREFIX = "agent-"
AGENT_WORKFLOW_SUFFIXES = (".yml", ".yaml")


def _classify_trigger(raw: str) -> tuple[bool, bool, bool, str]:
    """Return (manual_dispatch, scheduled, workflow_run_trigger, trigger_type)."""
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
    return manual_dispatch, scheduled, workflow_run_trigger, trigger_type


def _summarize(entries: Sequence[WorkflowHealthEntry]) -> str:
    """Human-readable health line for the Remediation tab banner."""
    if not entries:
        return (
            "No agent remediation workflows were found in .github/workflows/. "
            "The dashboard has no local automation surface to report on."
        )
    dormant = [entry.workflow_file for entry in entries if entry.trigger_type == "dormant"]
    flagged = [entry.workflow_file for entry in entries if entry.issues]
    parts = [f"Found {len(entries)} agent remediation workflow(s)."]
    if dormant:
        parts.append(f"Dormant (no manual, scheduled or workflow_run trigger): {', '.join(dormant)}.")
    if flagged:
        parts.append(f"Flagged for legacy or retired references: {', '.join(flagged)}.")
    if not dormant and not flagged:
        parts.append("All are triggerable and free of legacy references.")
    return " ".join(parts)


def inspect_remediation_workflows(repo_root: Path) -> WorkflowHealthReport:
    """Report health of the agent remediation workflows present in ``repo_root``.

    Discovers every ``.github/workflows/agent-*.yml`` file (case-insensitive)
    and classifies its trigger surface, flagging legacy dispatch patterns and
    references to workflows retired by RM#1483.
    """
    from time_utils import utc_now_iso

    workflows_dir = repo_root / ".github" / "workflows"
    entries: list[WorkflowHealthEntry] = []
    candidates = sorted(
        (
            path
            for path in (workflows_dir.iterdir() if workflows_dir.is_dir() else ())
            if path.is_file()
            and path.name.lower().startswith(AGENT_WORKFLOW_PREFIX)
            and path.suffix.lower() in AGENT_WORKFLOW_SUFFIXES
        ),
        key=lambda path: path.name,
    )
    for path in candidates:
        raw = path.read_text(encoding="utf-8")
        issues = [message for needle, message in LEGACY_WORKFLOW_PATTERNS if needle in raw]
        issues.extend(message for needle, message in RETIRED_WORKFLOW_PATTERNS if needle in raw)
        manual_dispatch, scheduled, workflow_run_trigger, trigger_type = _classify_trigger(raw)
        workflow_name_match = re.search(r"^name:\s*(.+)$", raw, re.MULTILINE)
        workflow_name = workflow_name_match.group(1).strip().strip('"').strip("'") if workflow_name_match else path.stem
        entries.append(
            WorkflowHealthEntry(
                workflow_file=path.name,
                workflow_name=workflow_name,
                exists=True,
                manual_dispatch=manual_dispatch,
                scheduled=scheduled,
                workflow_run_trigger=workflow_run_trigger,
                trigger_type=trigger_type,
                issues=tuple(issues),
            )
        )
    return WorkflowHealthReport(
        generated_at=utc_now_iso(),
        summary=_summarize(entries),
        workflows=tuple(entries),
    )


def inspect_jules_workflows(repo_root: Path) -> WorkflowHealthReport:
    """Deprecated alias for :func:`inspect_remediation_workflows`.

    The Jules workflow suite was retired fleet-wide by RM#1483. Kept for one
    release so any out-of-tree caller fails loudly on removal, not silently
    here.
    """
    return inspect_remediation_workflows(repo_root)
