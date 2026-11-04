"""Bridge to shared_scripts/queued_job_diagnostic.py for dashboard routes (#2897)."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PUBLIC_HEAVY_POLICY = _REPO_ROOT / "config" / "public_repo_heavy_jobs.json"


@lru_cache(maxsize=1)
def diagnostic_module() -> Any:
    """Load the shared queued-job diagnostic module from disk."""
    script = _REPO_ROOT / "shared_scripts" / "queued_job_diagnostic.py"
    spec = importlib.util.spec_from_file_location("queued_job_diagnostic", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load queued job diagnostic from {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("queued_job_diagnostic", module)
    spec.loader.exec_module(module)
    return module


def jobs_for_classification(sampled_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize /api/queue/diagnose job samples for ``classify_job``."""
    out: list[dict[str, Any]] = []
    for job in sampled_jobs:
        labels = [str(label) for label in job.get("labels", []) or [] if label]
        out.append(
            {
                "repo": str(job.get("repo") or "unknown"),
                "run_id": job.get("run_id", 0),
                "workflow": str(job.get("workflow") or ""),
                "job": str(job.get("job") or "unknown"),
                "labels": labels,
            }
        )
    return out


def load_public_heavy_policy() -> dict[str, Any]:
    """Return config/public_repo_heavy_jobs.json (empty dict on failure)."""
    try:
        data = json.loads(_PUBLIC_HEAVY_POLICY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}


def policy_provisioning_snapshot(runners: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-check documented public-heavy label demand vs online runner inventory."""
    policy = load_public_heavy_policy()
    group_label = str(policy.get("group_label") or "")
    online_with_label = 0
    if group_label:
        for runner in runners:
            if runner.get("status") != "online":
                continue
            labels = runner.get("labels", []) or []
            names = {str(label.get("name") if isinstance(label, dict) else label) for label in labels if label}
            if group_label in names:
                online_with_label += 1
    jobs = policy.get("jobs") if isinstance(policy.get("jobs"), list) else []
    return {
        "group_label": group_label,
        "environment": policy.get("environment"),
        "workflow_jobs": jobs,
        "online_runners_with_label": online_with_label,
        "note": (
            "Jobs listed in config/public_repo_heavy_jobs.json require this label; "
            "zero online runners with the label yields label_mismatch stalls."
        ),
    }


def _runner_groups_for_classification(
    runner_groups: dict[str, Any] | None,
    findings: list[str],
) -> dict[str, Any]:
    """Use group access metadata only when selected repos are present (list API is often partial)."""
    payload = runner_groups or {}
    groups = payload.get("runner_groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list) or not groups:
        return {}
    incomplete: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        if group.get("visibility") == "selected" and not group.get("selected_repositories"):
            name = str(group.get("name") or "unknown")
            incomplete.append(name)
    if incomplete:
        findings.append(
            "org runner-groups payload omitted selected_repositories for "
            + ", ".join(sorted(incomplete))
            + "; runner_group_access classification skipped"
        )
        return {}
    return payload if isinstance(payload, dict) else {}


def build_wait_reason_classification(
    *,
    sampled_jobs: list[dict[str, Any]],
    runners: list[dict[str, Any]],
    runner_groups: dict[str, Any] | None,
    api_findings: list[str] | None = None,
) -> dict[str, Any] | None:
    """Run shared ``analyze_snapshot`` on live samples (read-only)."""
    jobs = jobs_for_classification(sampled_jobs)
    if not jobs:
        return None
    findings = list(api_findings or [])
    groups_payload = _runner_groups_for_classification(runner_groups, findings)
    diag = diagnostic_module()
    report = diag.analyze_snapshot(
        queued_jobs=jobs,
        runners=[runner for runner in runners if isinstance(runner, dict)],
        runner_groups=groups_payload,
        api_findings=findings or None,
    )
    slim = {
        "classification_counts": report.get("classification_counts"),
        "classification_job_count": report.get("classification_job_count"),
        "top_causes": diag.top_causes(report.get("classification_counts") or {}),
        "runner_pool": report.get("runner_pool"),
        "runner_groups": report.get("runner_groups"),
        "f5_reserved_merge_group_label": report.get("f5_reserved_merge_group_label"),
        "api_findings": report.get("api_findings") or [],
    }
    return slim
