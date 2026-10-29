"""Private Mode PM-17 required-check wiring, SLA pending alert, hosted-runner guard, and 2-person fallback (#3268)."""

from __future__ import annotations

import datetime as _dt_mod
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from private_context_audit import PrivateModeAuditEvent, get_audit_store
from private_mode import (
    DISABLED,
    ClassificationError,
    ClassificationInput,
    ClassificationPolicy,
    Level,
    PrivateModeConfig,
    resolve,
    stamp_envelope,
)
from private_mode_approvals import _parse_iso_utc
from private_mode_emit import emit_shared

from .private_ci_controller import TARGET_KINDS, VALID_STATES
from .private_sandbox_executor import is_private_ci_enabled

_UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
DEFAULT_SLA_SECONDS = 900
DEFAULT_PRIVATE_RUNNER_LABELS: frozenset[str] = frozenset({"private-pool", "self-hosted-private"})
_HOSTED_RUNNER_PREFIXES = ("ubuntu-", "windows-", "macos-")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class ManualFallbackApproval:
    requester: str
    approver: str
    repo: str
    sha: str
    target_kind: str
    expires_at: str
    policy_version: str

    def __post_init__(self) -> None:
        for name in ("requester", "approver", "repo", "sha", "target_kind", "expires_at", "policy_version"):
            val = getattr(self, name)
            if not isinstance(val, str) or not val.strip():
                raise ClassificationError(f"ManualFallbackApproval: {name} must be a non-empty str")
        if not _REPO_RE.match(self.repo) or not _SHA_RE.match(self.sha):
            raise ClassificationError("ManualFallbackApproval: invalid repo or sha")
        if self.target_kind not in TARGET_KINDS:
            raise ClassificationError(f"ManualFallbackApproval: invalid target_kind {self.target_kind!r}")
        _parse_iso_utc(self.expires_at, "ManualFallbackApproval.expires_at")
        if self.requester.strip().lower() == self.approver.strip().lower():
            raise ClassificationError(
                "ManualFallbackApproval: self-approval is refused (requires 2 distinct principals)"
            )


@dataclass(frozen=True)
class RequiredCheckResult:
    enabled: bool
    state: str
    repo: str
    sha: str
    target_kind: str
    auto_passed: bool = False
    manual_override: bool = False
    sla_deadline: str | None = None
    alert_emitted: bool = False


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def _record_audit(
    action: str,
    actor: str,
    target_id: str,
    level: Level,
    outcome: str,
    meta: Mapping[str, str | int | bool],
    config: PrivateModeConfig,
    audit_store: Any | None,
) -> None:
    if not config.enabled and audit_store is None:
        return
    if not hasattr(_dt_mod, "UTC"):
        _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
    store = audit_store if audit_store is not None else get_audit_store()
    res = store.record_decision(
        PrivateModeAuditEvent(
            action=action, actor=actor, target_id=target_id, level=level, outcome=outcome, metadata=meta
        ),
        config=config,
    )
    if config.enabled and (res.parked or res.state != "OK"):
        raise ClassificationError("private_required_check: audit write failed (fail-closed)")


def issue_manual_fallback(
    *,
    requester: str,
    approver: str,
    repo: str,
    sha: str,
    target_kind: str,
    expires_at: str,
    policy_version: str,
    level: Level = Level.SENSITIVE,
    config: PrivateModeConfig = DISABLED,
    audit_store: Any | None = None,
) -> ManualFallbackApproval:
    """Issue and audit a two-principal manual required-check fallback bound to repo, SHA, and target_kind."""
    meta = {"repo": str(repo), "sha": str(sha), "target_kind": str(target_kind), "policy_version": str(policy_version)}
    try:
        record = ManualFallbackApproval(
            requester=requester.strip(),
            approver=approver.strip(),
            repo=repo.strip(),
            sha=sha.strip(),
            target_kind=target_kind.strip(),
            expires_at=expires_at.strip(),
            policy_version=policy_version.strip(),
        )
    except ClassificationError:
        _record_audit(
            "ci_fallback.issue",
            str(requester or "unknown"),
            f"{repo}@{sha}",
            level,
            "refused",
            meta,
            config,
            audit_store,
        )
        raise
    _record_audit(
        "ci_fallback.issue", record.approver, f"{record.repo}@{record.sha}", level, "issued", meta, config, audit_store
    )
    return record


def _extract_runner_labels(runs_on: Any) -> list[str]:
    if isinstance(runs_on, str):
        return [runs_on.strip()]
    if isinstance(runs_on, Sequence) and not isinstance(runs_on, (bytes, bytearray)):
        return [str(x).strip() for x in runs_on if str(x).strip()]
    if isinstance(runs_on, Mapping):
        grp = runs_on.get("group") or runs_on.get("labels") or ""
        return _extract_runner_labels(grp)
    return []


def verify_no_hosted_workflows_for_private_repo(
    repo: str,
    workflows: Sequence[Mapping[str, Any]],
    *,
    overlay_policy: ClassificationPolicy,
    private_mode: PrivateModeConfig,
    allowed_private_runners: frozenset[str] = DEFAULT_PRIVATE_RUNNER_LABELS,
) -> Level:
    """Resolve repo classification solely from the overlay and fail closed if a private repo uses a hosted runner."""
    level = resolve(ClassificationInput(repo=repo), policy=overlay_policy, config=private_mode)
    if level is Level.PUBLIC:
        return level
    for wf in workflows:
        jobs = wf.get("jobs") if isinstance(wf, Mapping) else None
        if not isinstance(jobs, Mapping):
            raise ClassificationError("private_required_check: workflow must define a jobs mapping")
        for job_id, job_spec in jobs.items():
            labels = _extract_runner_labels(job_spec.get("runs-on") if isinstance(job_spec, Mapping) else None)
            if not labels:
                raise ClassificationError(f"private_required_check: job {job_id!r} missing runs-on")
            for label in labels:
                low = label.lower()
                if low.startswith(_HOSTED_RUNNER_PREFIXES) or label not in allowed_private_runners:
                    raise ClassificationError(
                        f"private_required_check: hosted runner {label!r} forbidden on private repo (job {job_id!r})"
                    )
    return level


class PrivateRequiredCheckWiring:
    """Required-check wiring enforcing SLA pending alerts, hosted-runner ban, and 2-person manual fallback."""

    def __init__(
        self,
        *,
        overlay_policy: ClassificationPolicy,
        private_mode: PrivateModeConfig = DISABLED,
        sla_seconds: int = DEFAULT_SLA_SECONDS,
        allowed_private_runners: frozenset[str] = DEFAULT_PRIVATE_RUNNER_LABELS,
        alert_sink: Callable[[dict[str, Any]], Any] | None = None,
        audit_store: Any | None = None,
    ) -> None:
        if not isinstance(overlay_policy, ClassificationPolicy):
            raise ClassificationError("PrivateRequiredCheckWiring: overlay_policy must be a ClassificationPolicy")
        if isinstance(sla_seconds, bool) or not isinstance(sla_seconds, int) or sla_seconds <= 0:
            raise ClassificationError("PrivateRequiredCheckWiring: sla_seconds must be a positive int")
        self._policy, self._pm, self._sla = overlay_policy, private_mode, sla_seconds
        self._runners, self._alert_sink, self._audit = allowed_private_runners, alert_sink, audit_store

    def evaluate_check(
        self,
        *,
        run_id: str,
        repo: str,
        sha: str,
        target_kind: str,
        pool_healthy: bool,
        workflows: Sequence[Mapping[str, Any]],
        enabled: bool | None = None,
        attested_state: str = "success",
        now: str | None = None,
    ) -> RequiredCheckResult:
        if not _REPO_RE.match(repo) or not _SHA_RE.match(sha) or target_kind not in TARGET_KINDS:
            raise ClassificationError("evaluate_check: invalid repo, sha, or target_kind")
        if attested_state not in VALID_STATES:
            raise ClassificationError(f"evaluate_check: invalid attested_state {attested_state!r}")
        if not is_private_ci_enabled(enabled=enabled):
            return RequiredCheckResult(False, "disabled", repo, sha, target_kind)
        level = verify_no_hosted_workflows_for_private_repo(
            repo,
            workflows,
            overlay_policy=self._policy,
            private_mode=self._pm,
            allowed_private_runners=self._runners,
        )
        if not pool_healthy:
            now_dt = _parse_iso_utc(now, "evaluate_check.now") if now else datetime.now(_UTC)
            deadline = _format_iso(now_dt + timedelta(seconds=self._sla))
            env = stamp_envelope(level, hop="private_ci_sla_alert", config=self._pm)
            emit_shared(
                "alerts",
                {
                    "id": run_id,
                    "repo": repo,
                    "state": "pending",
                    "status": "warning",
                    "kind": "warning",
                    "degraded": True,
                    "ts": deadline,
                },
                level=env,
                config=self._pm,
                sink=self._alert_sink,
            )
            return RequiredCheckResult(
                True, "pending", repo, sha, target_kind, auto_passed=False, sla_deadline=deadline, alert_emitted=True
            )
        return RequiredCheckResult(True, attested_state, repo, sha, target_kind, auto_passed=False)

    def apply_manual_fallback(
        self,
        approval: ManualFallbackApproval,
        *,
        repo: str,
        sha: str,
        target_kind: str,
        policy_version: str,
        now: str | None = None,
    ) -> RequiredCheckResult:
        if not isinstance(approval, ManualFallbackApproval):
            raise ClassificationError("apply_manual_fallback: approval must be a ManualFallbackApproval")
        level = resolve(ClassificationInput(repo=repo), policy=self._policy, config=self._pm)
        meta = {"repo": repo, "sha": sha, "target_kind": target_kind, "policy_version": policy_version}
        if (approval.repo, approval.sha, approval.target_kind, approval.policy_version) != (
            repo,
            sha,
            target_kind,
            policy_version,
        ):
            _record_audit(
                "ci_fallback.verify",
                approval.approver,
                f"{repo}@{sha}",
                level,
                "refused-mismatch",
                meta,
                self._pm,
                self._audit,
            )
            raise ClassificationError("apply_manual_fallback: fallback approval binding mismatch")
        now_dt = _parse_iso_utc(now, "apply_manual_fallback.now") if now else datetime.now(_UTC)
        if now_dt >= _parse_iso_utc(approval.expires_at, "apply_manual_fallback.expires_at"):
            _record_audit(
                "ci_fallback.verify",
                approval.approver,
                f"{repo}@{sha}",
                level,
                "refused-expired",
                meta,
                self._pm,
                self._audit,
            )
            raise ClassificationError("apply_manual_fallback: fallback approval has expired")
        _record_audit(
            "ci_fallback.verify", approval.approver, f"{repo}@{sha}", level, "approved", meta, self._pm, self._audit
        )
        return RequiredCheckResult(True, "success", repo, sha, target_kind, auto_passed=False, manual_override=True)
