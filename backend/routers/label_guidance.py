"""Runner label guidance and audit routes.

Issue #757 — workflow routing guidance for NVMe, HDD, Docker, and bulk labels.

Routes:
  GET  /api/runners/label-guidance   Per-label workload guidance + runs-on snippets
  GET  /api/runners/label-audit      Offline audit of workflow routing policy
"""

from __future__ import annotations

import datetime as _dt_mod
import json
import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("dashboard.label_guidance")
router = APIRouter(tags=["runners"])

# ─── Constants ────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATH = _REPO_ROOT / "config" / "workflow_runner_routing_policy.json"

UTC = _dt_mod.UTC

# Canonical label guidance (extends the policy file with UX-facing fields).
# Any label present in the policy taxonomy is described here.
_LABEL_GUIDANCE: dict[str, dict[str, str]] = {
    "d-sorg-fleet-nvme": {
        "purpose": ("Physical NVMe-tier label — use when a job must be placed on the fastest available storage pool."),
        "workload": (
            "Compile-heavy native builds, large dependency installs, "
            "Playwright suites, cache-intensive jobs that benefit from "
            "sub-millisecond disk latency."
        ),
        "avoid_for": (
            "Lightweight governance, docs checks, secret scans, or any "
            "read-mostly maintenance workflow that does not generate "
            "high disk I/O."
        ),
        "runs_on_snippet": ("runs-on: [self-hosted, Linux, X64, d-sorg-fleet-nvme]"),
    },
    "d-sorg-fleet-fast-io": {
        "purpose": (
            "Capability label for workflows that need high local I/O without requiring explicit NVMe placement."
        ),
        "workload": (
            "CI test suites, lockfile refreshes, dependency installs, Jules Auto-Repair / PR-AutoFix, release builds."
        ),
        "avoid_for": ("Pure documentation or governance workflows that do not exercise disk I/O."),
        "runs_on_snippet": ("runs-on: [self-hosted, Linux, X64, d-sorg-fleet-fast-io]"),
    },
    "d-sorg-fleet-docker": {
        "purpose": (
            "Container-build label for Docker image builds, Trivy security scans, and jobs that churn layered caches."
        ),
        "workload": (
            "docker build / buildx, container image pushes, Trivy scans, "
            "multi-stage builds, layer-cache intensive jobs."
        ),
        "avoid_for": (
            "Lightweight maintenance workflows and bulk governance jobs that do not build or scan container images."
        ),
        "runs_on_snippet": ("runs-on: [self-hosted, Linux, X64, d-sorg-fleet-docker]"),
    },
    "d-sorg-fleet-bulk": {
        "purpose": (
            "Default HDD-tier label for lightweight audits, governance "
            "workflows, docs checks, and read-mostly maintenance."
        ),
        "workload": (
            "Issue taxonomy sync, label sync, spec checks, secret scans, "
            "lease reaper, verify-tag, agent-fleet-dashboard regeneration, "
            "and other low-I/O scheduled maintenance."
        ),
        "avoid_for": (
            "Docker image builds, native-build CI, lockfile refreshes, Playwright or any cache-heavy test suite."
        ),
        "runs_on_snippet": ("runs-on: [self-hosted, Linux, X64, d-sorg-fleet-bulk]"),
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _load_policy() -> dict[str, Any]:
    """Load the routing policy JSON from disk.

    Returns an empty dict on read/parse failure so the endpoint degrades
    gracefully without a 5xx.
    """
    try:
        text = _POLICY_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("label_guidance: could not load policy from %s: %s", _POLICY_PATH, exc)
        return {}


def _load_workflow_labels(workflow_path: Path) -> list[tuple[str, list[str]]]:
    """Return (job_name, labels) pairs for every job in a workflow file."""
    try:
        data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        # PyYAML parses the bare 'on:' key as True
        if True in data and "on" not in data:
            data["on"] = data[True]
        jobs = data.get("jobs") or {}
        if not isinstance(jobs, dict):
            return []
        result: list[tuple[str, list[str]]] = []
        for job_name, job_body in jobs.items():
            if not isinstance(job_body, dict):
                continue
            if "uses" in job_body and "steps" not in job_body:
                continue  # reusable workflow call — no runs-on
            runs_on = job_body.get("runs-on")
            if runs_on is None:
                continue
            if isinstance(runs_on, str):
                labels = [runs_on]
            elif isinstance(runs_on, list):
                labels = [str(item) for item in runs_on]
            else:
                labels = [str(runs_on)]
            result.append((str(job_name), labels))
        return result
    except Exception as exc:  # noqa: BLE001
        log.debug("label_guidance: could not parse %s: %s", workflow_path, exc)
        return []


def _run_policy_audit(policy: dict[str, Any], workflow_dir: Path) -> dict[str, Any]:
    """Check configured runners against the taxonomy; return structured findings."""
    taxonomy: dict[str, Any] = policy.get("label_taxonomy") or {}
    neutral: set[str] = set(policy.get("neutral_labels") or [])
    known_labels: set[str] = set(taxonomy) | neutral
    workflow_classes: dict[str, Any] = policy.get("workflow_classes") or {}

    policy_errors: list[str] = []
    violations: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    # Validate that all workflow files referenced in the policy actually exist.
    if workflow_dir.is_dir():
        existing_names = {p.name for p in workflow_dir.glob("*.yml")}
    else:
        existing_names = set()
        policy_errors.append(f"workflow directory not found: {workflow_dir}")

    for class_name, class_policy in workflow_classes.items():
        if not isinstance(class_policy, dict):
            policy_errors.append(f"{class_name}: workflow class policy must be a mapping")
            continue
        recommended: list[str] = list(class_policy.get("recommended_labels") or [])
        forbidden: set[str] = set(class_policy.get("forbidden_labels") or [])
        description: str = str(class_policy.get("description") or class_name)

        for label_group in ("recommended_labels", "forbidden_labels"):
            for label in class_policy.get(label_group) or []:
                if label not in known_labels:
                    policy_errors.append(f"{class_name}: unknown label '{label}' in {label_group}")

        for workflow_name in class_policy.get("workflows") or []:
            if workflow_name not in existing_names:
                policy_errors.append(f"{class_name}: workflow file not found: {workflow_name}")
                continue
            for job_name, labels in _load_workflow_labels(workflow_dir / workflow_name):
                label_set = set(labels)
                unknown_tier = sorted(
                    lbl for lbl in label_set if lbl.startswith("d-sorg-fleet-") and lbl not in known_labels
                )
                if unknown_tier:
                    violations.append(
                        {
                            "severity": "violation",
                            "workflow": workflow_name,
                            "job": job_name,
                            "labels": sorted(label_set),
                            "message": (
                                f"{class_name} workflow uses unknown tier label(s): " + ", ".join(unknown_tier)
                            ),
                            "recommended_labels": recommended,
                        }
                    )
                    continue
                bad_labels = sorted(lbl for lbl in label_set if lbl in forbidden)
                if bad_labels:
                    violations.append(
                        {
                            "severity": "violation",
                            "workflow": workflow_name,
                            "job": job_name,
                            "labels": sorted(label_set),
                            "message": (
                                f"{class_name} workflow is pinned to forbidden tier label(s): " + ", ".join(bad_labels)
                            ),
                            "recommended_labels": recommended,
                        }
                    )
                    continue
                if any(lbl in recommended for lbl in label_set):
                    continue
                if label_set and label_set.issubset(neutral):
                    recommendations.append(
                        {
                            "severity": "recommendation",
                            "workflow": workflow_name,
                            "job": job_name,
                            "labels": sorted(label_set),
                            "message": (f"{description} This workflow is still on a neutral label."),
                            "recommended_labels": recommended,
                        }
                    )

    return {
        "ok": not violations and not policy_errors,
        "policy_errors": policy_errors,
        "violations": violations,
        "recommendations": recommendations,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("/api/runners/label-guidance")
async def get_label_guidance() -> JSONResponse:
    """Return structured workflow routing guidance for each runner label.

    The response includes:
    - ``taxonomy``: per-label workload description and copy-paste ``runs-on`` snippet.
    - ``neutral_labels``: labels safe to use during the transition period.
    - ``workflow_classes``: workload tiers with recommended and forbidden labels.
    - ``generated_at``: ISO-8601 timestamp.
    """
    policy = _load_policy()
    neutral_labels: list[str] = list(policy.get("neutral_labels") or ["d-sorg-fleet", "self-hosted"])
    workflow_classes: dict[str, Any] = {}
    for cls_name, cls_policy in (policy.get("workflow_classes") or {}).items():
        if not isinstance(cls_policy, dict):
            continue
        workflow_classes[cls_name] = {
            "description": cls_policy.get("description", cls_name),
            "recommended_labels": list(cls_policy.get("recommended_labels") or []),
            "forbidden_labels": list(cls_policy.get("forbidden_labels") or []),
        }

    # Merge static guidance with policy taxonomy (policy wins on 'purpose').
    taxonomy: dict[str, Any] = {}
    for label, static in _LABEL_GUIDANCE.items():
        entry: dict[str, Any] = dict(static)
        policy_taxonomy: dict[str, Any] = policy.get("label_taxonomy") or {}
        if label in policy_taxonomy and isinstance(policy_taxonomy[label], dict):
            policy_purpose = policy_taxonomy[label].get("purpose")
            if policy_purpose:
                entry["purpose"] = policy_purpose
        taxonomy[label] = entry

    return JSONResponse(
        {
            "taxonomy": taxonomy,
            "neutral_labels": neutral_labels,
            "workflow_classes": workflow_classes,
            "generated_at": _dt_mod.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    )


@router.get("/api/runners/label-audit")
async def get_label_audit() -> JSONResponse:
    """Run the offline workflow-routing audit and return structured findings.

    Reads ``config/workflow_runner_routing_policy.json`` and the workflow
    directory without requiring WSL or a GitHub API token.

    Returns:
    - ``ok``: ``true`` when no violations or policy errors were found.
    - ``policy_errors``: configuration problems (unknown labels, missing files).
    - ``violations``: workflows explicitly misrouted to a forbidden tier.
    - ``recommendations``: workflows still on neutral labels that should migrate.
    - ``policy_source``: absolute path to the policy file that was read.
    - ``generated_at``: ISO-8601 timestamp.
    """
    policy = _load_policy()
    workflow_dir = _REPO_ROOT / ".github" / "workflows"

    if policy:
        findings = _run_policy_audit(policy, workflow_dir)
    else:
        findings = {
            "ok": False,
            "policy_errors": [f"policy file could not be loaded: {_POLICY_PATH}"],
            "violations": [],
            "recommendations": [],
        }

    return JSONResponse(
        {
            **findings,
            "policy_source": str(_POLICY_PATH),
            "generated_at": _dt_mod.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    )
