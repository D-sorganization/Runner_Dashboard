"""Shared required-context names for deploy CI gate tests."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def policy_required_context_names() -> tuple[str, ...]:
    path = REPO / "config" / "required_status_checks_policy.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for item in raw.get("required_contexts") or []:
        if isinstance(item, dict) and item.get("context"):
            names.append(str(item["context"]))
    return tuple(dict.fromkeys(names))


def success_runs_for_required_contexts(
    sha: str, extra: list[dict[str, object]] | None = None
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    for index, name in enumerate(policy_required_context_names(), start=1):
        runs.append(
            {
                "id": index,
                "head_sha": sha,
                "name": name,
                "status": "completed",
                "conclusion": "success",
                "app": {"id": 1},
                "check_suite": {"id": index},
                "started_at": "2026-01-01T00:00:00Z",
            }
        )
    if extra:
        runs.extend(extra)
    return runs
