#!/usr/bin/env python3
"""Read-only queued-job diagnostic: classify queued jobs vs runner capacity (#2897)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

LOG = logging.getLogger("queued_job_diagnostic")
_FLEET_PREFIX = "d-sorg-fleet"
_MERGE_GROUP_LABEL = "d-sorg-fleet-merge-group"


class DiagnosticPreconditionError(ValueError):
    pass


class WaitReason(StrEnum):
    LABEL_MISMATCH = "label_mismatch"
    NEEDS_BLOCKED = "needs_blocked"
    CONCURRENCY_BLOCKED = "concurrency_blocked"
    RUNNER_GROUP_ACCESS = "runner_group_access"
    WAITING_FOR_RUNNER = "waiting_for_runner"
    GITHUB_HOSTED = "github_hosted"
    UNKNOWN = "unknown"


def is_routable(required_labels: Sequence[str], online_label_sets: list[frozenset[str]]) -> bool:
    required = frozenset(label for label in required_labels if label)
    if not required:
        return True
    return any(required <= labels for labels in online_label_sets)


@dataclass(frozen=True, slots=True)
class ClassifiedJob:
    repo: str
    run_id: int | str
    workflow: str
    job: str
    labels: tuple[str, ...]
    reason: WaitReason
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {**{k: getattr(self, k) for k in ("repo", "run_id", "workflow", "job")}, "labels": list(self.labels), "reason": self.reason.value, "detail": self.detail}


def _runner_labels(runner: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for label in runner.get("labels", []) or []:
        name = label.get("name") if isinstance(label, dict) else str(label)
        if name:
            out.append(str(name))
    return out


def _label_sets(runners: Sequence[Mapping[str, Any]], *, idle_only: bool) -> list[frozenset[str]]:
    sets: list[frozenset[str]] = []
    for runner in runners:
        if runner.get("status") != "online" or (idle_only and runner.get("busy")):
            continue
        sets.append(frozenset(_runner_labels(runner)))
    return sets


def _group_access_index(runner_groups: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for group in runner_groups.get("runner_groups") or []:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or "")
        if not name:
            continue
        repos = [str(r.get("name") if isinstance(r, dict) else r) for r in group.get("selected_repositories") or []]
        index[name] = {"visibility": str(group.get("visibility") or "unknown"), "repos_allowed": sorted(set(repos))}
    return index


def _fleet_group(labels: Sequence[str]) -> str | None:
    fleet = [label for label in labels if label == _FLEET_PREFIX or label.startswith(f"{_FLEET_PREFIX}-")]
    return fleet[-1] if fleet else None


def _repo_allowed(repo: str, group: str, access: Mapping[str, Mapping[str, Any]]) -> bool:
    meta = access.get(group)
    if meta is None or meta.get("visibility") in ("all", "public", "organization"):
        return True
    allowed = meta.get("repos_allowed") or []
    return bool(allowed) and repo in allowed if meta.get("visibility") == "selected" else True


def classify_job(
    job: Mapping[str, Any],
    *,
    online: list[frozenset[str]],
    idle: list[frozenset[str]],
    access: Mapping[str, Mapping[str, Any]],
) -> ClassifiedJob:
    labels = [str(x) for x in job.get("labels", []) or [] if x]
    base = (str(job.get("repo") or "unknown"), job.get("run_id", 0), str(job.get("workflow") or ""), str(job.get("job") or "unknown"), tuple(labels))
    if job.get("blocked_by_needs"):
        return ClassifiedJob(*base, WaitReason.NEEDS_BLOCKED, "needs: graph")
    if job.get("blocked_by_concurrency"):
        return ClassifiedJob(*base, WaitReason.CONCURRENCY_BLOCKED, "concurrency group")
    if "self-hosted" not in {x.lower() for x in labels}:
        return ClassifiedJob(*base, WaitReason.GITHUB_HOSTED, "github-hosted queue")
    group = _fleet_group(labels)
    if group and not _repo_allowed(base[0], group, access):
        return ClassifiedJob(*base, WaitReason.RUNNER_GROUP_ACCESS, f"repo blocked for {group}")
    if labels and online and not is_routable(labels, online):
        return ClassifiedJob(*base, WaitReason.LABEL_MISMATCH, "no online runner has all labels")
    if labels and online and is_routable(labels, online):
        detail = "waiting for free runner capacity"
        if idle and is_routable(labels, idle):
            detail = "routable; queued behind other work or pickup lag"
        return ClassifiedJob(*base, WaitReason.WAITING_FOR_RUNNER, detail)
    return ClassifiedJob(*base, WaitReason.UNKNOWN, "insufficient metadata")


def _explain_idle(utilization: Mapping[str, Any] | None, counts: Counter[str]) -> dict[str, Any]:
    if not utilization:
        return {"explained_idle_fraction": None, "dominant_cause": "unknown", "label_and_repo_access_minutes": None}
    idle = float(utilization["idle_fraction"])
    mismatch_min = float(utilization.get("label_and_repo_access_minutes", 0))
    mismatch_frac = min(idle, mismatch_min / (24 * 60)) if mismatch_min else 0.0
    gap_frac = max(0.0, idle - mismatch_frac)
    explained = gap_frac + mismatch_frac
    dominant = "inter_job_gap"
    if counts["label_mismatch"] + counts["runner_group_access"] >= counts["waiting_for_runner"]:
        dominant = "label_or_group_access"
    return {
        "measured_idle_fraction": idle,
        "explained_idle_fraction": explained,
        "inter_job_gap_fraction": gap_frac,
        "label_and_repo_access_fraction": mismatch_frac,
        "label_and_repo_access_minutes": int(mismatch_min),
        "median_inter_job_gap_seconds": float(utilization.get("median_inter_job_gap_seconds", 0)),
        "jobs_under_60s_fraction": float(utilization.get("jobs_under_60s_fraction", 0)),
        "dominant_cause": dominant,
        "within_ten_percent": abs(explained - idle) <= 0.10,
    }


def _merge_group(labels: Sequence[str], workflow: str) -> bool:
    return _MERGE_GROUP_LABEL in labels or "merge-group" in workflow.lower()


def _f5(classified: Sequence[ClassifiedJob], idle: Mapping[str, Any]) -> dict[str, Any]:
    merge = [j for j in classified if _merge_group(j.labels, j.workflow)]
    waiting = [j for j in merge if j.reason == WaitReason.WAITING_FOR_RUNNER]
    proceed = len(waiting) >= max(1, len(merge) // 2) and len(waiting) >= 2
    if idle.get("dominant_cause") == "inter_job_gap" and len(waiting) <= 1:
        proceed = False
    return {
        "proceed": proceed,
        "rationale": (
            "Defer reserved merge_group label: idle time is mostly inter-job gaps."
            if not proceed
            else "Proceed: merge-group jobs are waiting for dedicated capacity."
        ),
        "merge_group_jobs_waiting": len(waiting),
        "merge_group_jobs_total": len(merge),
    }


def _group_report(classified: Sequence[ClassifiedJob], runners: Sequence[Mapping[str, Any]], access: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    waiting: dict[str, set[str]] = {}
    for job in classified:
        g = _fleet_group(job.labels)
        if g:
            waiting.setdefault(g, set()).add(job.repo)
    counts: dict[str, int] = {}
    for runner in runners:
        if runner.get("status") == "online":
            for label in _runner_labels(runner):
                if label.startswith(_FLEET_PREFIX):
                    counts[label] = counts.get(label, 0) + 1
    return [
        {
            "name": name,
            "visibility": access.get(name, {}).get("visibility", "unknown"),
            "repos_allowed": access.get(name, {}).get("repos_allowed", []),
            "online_runners_with_label": counts.get(name, 0),
            "queued_jobs_waiting": len(waiting.get(name, set())),
            "waiting_repos": sorted(waiting.get(name, set())),
        }
        for name in sorted(set(waiting) | set(access))
    ]


def analyze_snapshot(
    *,
    queued_jobs: Sequence[Mapping[str, Any]],
    runners: Sequence[Mapping[str, Any]],
    runner_groups: Mapping[str, Any] | None = None,
    utilization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not queued_jobs:
        raise DiagnosticPreconditionError("queued_jobs must be non-empty")
    online, idle = _label_sets(runners, idle_only=False), _label_sets(runners, idle_only=True)
    access = _group_access_index(runner_groups or {})
    classified = [classify_job(j, online=online, idle=idle, access=access) for j in queued_jobs]
    counts = Counter(j.reason.value for j in classified)
    idle_explanation = _explain_idle(utilization, counts)
    return {
        "classification_counts": dict(counts),
        "jobs": [j.to_dict() for j in classified],
        "runner_pool": {"online": len(online), "idle": len(idle)},
        "runner_groups": _group_report(classified, runners, access),
        "idle_explanation": idle_explanation,
        "f5_reserved_merge_group_label": _f5(classified, idle_explanation),
    }


def _load(path: Path) -> Any:
    if not path.is_file():
        raise DiagnosticPreconditionError(f"missing input file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_runners(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("runners"), list):
        return [x for x in payload["runners"] if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    raise DiagnosticPreconditionError("runners JSON must be a list or {runners: [...]}")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Classify queued GitHub Actions jobs (read-only).")
    p.add_argument("--queued-jobs", type=Path, required=True)
    p.add_argument("--runners", type=Path, required=True)
    p.add_argument("--runner-groups", type=Path)
    p.add_argument("--utilization", type=Path)
    args = p.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO)
    try:
        jobs = _load(args.queued_jobs)
        if not isinstance(jobs, list):
            raise DiagnosticPreconditionError("--queued-jobs must be a JSON array")
        groups = _load(args.runner_groups) if args.runner_groups else {}
        util = _load(args.utilization) if args.utilization else None
        report = analyze_snapshot(
            queued_jobs=jobs,
            runners=_parse_runners(_load(args.runners)),
            runner_groups=groups if isinstance(groups, dict) else {},
            utilization=util if isinstance(util, dict) else None,
        )
    except DiagnosticPreconditionError as exc:
        LOG.error("::error::%s", exc)
        return 2
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
