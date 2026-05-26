#!/usr/bin/env python3
"""Audit workflow runner labels against the tier-routing policy for issue #757."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Finding:
    severity: str
    workflow: str
    job: str
    labels: list[str]
    message: str
    recommended_labels: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "workflow": self.workflow,
            "job": self.job,
            "labels": self.labels,
            "message": self.message,
            "recommended_labels": self.recommended_labels,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must decode to a JSON object")
    return data


def _load_workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    if True in data and "on" not in data:
        data["on"] = data[True]
    return data


def _extract_labels(runs_on: Any) -> list[str]:
    if runs_on is None:
        return []
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [str(item) for item in runs_on]
    return [str(runs_on)]


def _workflow_job_labels(workflow_path: Path) -> list[tuple[str, list[str]]]:
    data = _load_workflow(workflow_path)
    jobs = data.get("jobs") or {}
    if not isinstance(jobs, dict):
        raise ValueError(f"{workflow_path} has no usable jobs block")

    job_labels: list[tuple[str, list[str]]] = []
    for job_name, job_body in jobs.items():
        if not isinstance(job_body, dict):
            continue
        if "uses" in job_body and "steps" not in job_body:
            continue
        labels = _extract_labels(job_body.get("runs-on"))
        if labels:
            job_labels.append((str(job_name), labels))
    return job_labels


def _validate_policy(policy: dict[str, Any], workflow_dir: Path) -> list[str]:
    errors: list[str] = []
    workflow_names = {path.name for path in workflow_dir.glob("*.yml")}
    taxonomy = policy.get("label_taxonomy") or {}
    neutral = set(policy.get("neutral_labels") or [])
    known_labels = set(taxonomy) | neutral

    classes = policy.get("workflow_classes") or {}
    if not isinstance(classes, dict):
        return ["workflow_classes must be a mapping"]

    for class_name, class_policy in classes.items():
        if not isinstance(class_policy, dict):
            errors.append(f"{class_name}: workflow class policy must be a mapping")
            continue
        workflows = class_policy.get("workflows") or []
        for workflow in workflows:
            if workflow not in workflow_names:
                errors.append(f"{class_name}: unknown workflow {workflow}")
        for label_group in ("recommended_labels", "forbidden_labels"):
            for label in class_policy.get(label_group) or []:
                if label not in known_labels:
                    errors.append(f"{class_name}: unknown label {label} in {label_group}")

    return errors


def audit_policy(policy_path: Path, workflow_dir: Path) -> dict[str, Any]:
    policy = _load_json(policy_path)
    policy_errors = _validate_policy(policy, workflow_dir)
    if policy_errors:
        return {
            "ok": False,
            "policy_errors": policy_errors,
            "violations": [],
            "recommendations": [],
        }

    neutral_labels = set(policy.get("neutral_labels") or [])
    workflow_classes = policy.get("workflow_classes") or {}
    taxonomy_labels = set((policy.get("label_taxonomy") or {}).keys())

    violations: list[Finding] = []
    recommendations: list[Finding] = []

    for class_name, class_policy in workflow_classes.items():
        recommended = list(class_policy.get("recommended_labels") or [])
        forbidden = set(class_policy.get("forbidden_labels") or [])
        description = str(class_policy.get("description") or class_name)
        for workflow_name in class_policy.get("workflows") or []:
            workflow_path = workflow_dir / workflow_name
            for job_name, labels in _workflow_job_labels(workflow_path):
                label_set = set(labels)
                unknown_tier_labels = sorted(
                    label for label in label_set if label.startswith("d-sorg-fleet-") and label not in taxonomy_labels
                )
                if unknown_tier_labels:
                    violations.append(
                        Finding(
                            severity="violation",
                            workflow=workflow_name,
                            job=job_name,
                            labels=sorted(label_set),
                            message=(
                                f"{class_name} workflow uses unknown tier label(s): " + ", ".join(unknown_tier_labels)
                            ),
                            recommended_labels=recommended,
                        )
                    )
                    continue

                bad_labels = sorted(label for label in label_set if label in forbidden)
                if bad_labels:
                    violations.append(
                        Finding(
                            severity="violation",
                            workflow=workflow_name,
                            job=job_name,
                            labels=sorted(label_set),
                            message=(
                                f"{class_name} workflow is pinned to forbidden tier label(s): " + ", ".join(bad_labels)
                            ),
                            recommended_labels=recommended,
                        )
                    )
                    continue

                if any(label in recommended for label in label_set):
                    continue

                if label_set and label_set.issubset(neutral_labels):
                    recommendations.append(
                        Finding(
                            severity="recommendation",
                            workflow=workflow_name,
                            job=job_name,
                            labels=sorted(label_set),
                            message=f"{description} This workflow is still on a neutral label.",
                            recommended_labels=recommended,
                        )
                    )

    return {
        "ok": not violations,
        "policy_errors": [],
        "violations": [finding.to_dict() for finding in violations],
        "recommendations": [finding.to_dict() for finding in recommendations],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    root = _repo_root()
    parser.add_argument(
        "--policy",
        type=Path,
        default=root / "config" / "workflow_runner_routing_policy.json",
        help="Path to the runner routing policy JSON file.",
    )
    parser.add_argument(
        "--workflow-dir",
        type=Path,
        default=root / ".github" / "workflows",
        help="Path to the workflow directory to audit.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    return parser.parse_args()


def _print_text(result: dict[str, Any]) -> None:
    policy_errors = result["policy_errors"]
    violations = result["violations"]
    recommendations = result["recommendations"]

    if policy_errors:
        print("Workflow runner routing policy is invalid:")
        for error in policy_errors:
            print(f"- {error}")
        return

    print(f"Workflow runner routing audit: {len(violations)} violation(s), {len(recommendations)} recommendation(s).")
    for item in violations:
        print(
            f"VIOLATION {item['workflow']}::{item['job']} labels={item['labels']}: "
            f"{item['message']} Recommend {item['recommended_labels']}."
        )
    for item in recommendations:
        print(
            f"RECOMMEND {item['workflow']}::{item['job']} labels={item['labels']}: "
            f"{item['message']} Prefer {item['recommended_labels']}."
        )


def main() -> int:
    args = _parse_args()
    result = audit_policy(args.policy, args.workflow_dir)
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_text(result)
    return 0 if result["ok"] and not result["policy_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
