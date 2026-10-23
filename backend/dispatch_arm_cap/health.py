"""Pure healthy-arm classification for armed PRs (no I/O except policy JSON paths)."""

from __future__ import annotations

import json

from dispatch_arm_cap.types import (
    CONFLICT_DISARMED_LABEL,
    ArmCapPolicy,
    HealthVerdict,
    Lifecycle,
    PrArmSnapshot,
)

_FAILING_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required"})


def load_required_contexts(policy: ArmCapPolicy) -> tuple[str, ...]:
    """Required status contexts for healthy-arm counting (RM-OPS-004 when live file exists)."""
    for path in (policy.required_checks_path, policy.fallback_checks_path):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and "required_contexts" in raw:
            contexts = raw.get("required_contexts") or []
            names: list[str] = []
            for item in contexts:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict) and item.get("context"):
                    names.append(str(item["context"]))
            if names:
                return tuple(names)
        if isinstance(raw, dict) and "repositories" in raw:
            merged: list[str] = []
            repos = raw.get("repositories")
            if isinstance(repos, dict):
                for entry in repos.values():
                    if isinstance(entry, dict):
                        for ctx in entry.get("contexts") or entry.get("required_contexts") or []:
                            if isinstance(ctx, str):
                                merged.append(ctx)
                            elif isinstance(ctx, dict) and ctx.get("context"):
                                merged.append(str(ctx["context"]))
            if merged:
                return tuple(dict.fromkeys(merged))
    return ()


def classify_arm_health(snapshot: PrArmSnapshot, required_contexts: tuple[str, ...]) -> HealthVerdict:
    """Count only healthy armed PRs (issue #2029 acceptance)."""
    if not snapshot.armed or snapshot.draft:
        return HealthVerdict(False, "unarmed", ("not armed",))
    lowered = {label.lower() for label in snapshot.labels}
    if CONFLICT_DISARMED_LABEL in lowered:
        return HealthVerdict(False, "evicted", (f"`{CONFLICT_DISARMED_LABEL}` label",))
    if snapshot.mergeable_state == "dirty":
        return HealthVerdict(False, "evicted", ("mergeable_state=dirty (CONFLICTING)",))
    failing: list[str] = []
    if required_contexts and snapshot.check_runs:
        by_name = {name: conclusion for name, conclusion in snapshot.check_runs}
        for ctx in required_contexts:
            conclusion = by_name.get(ctx, "")
            if conclusion in _FAILING_CONCLUSIONS:
                failing.append(f"required check {ctx}={conclusion}")
    if failing:
        return HealthVerdict(False, "evicted", tuple(failing))
    if snapshot.in_merge_queue:
        lifecycle: Lifecycle = "building" if snapshot.merge_queue_state in {"merging", "merged"} else "queued"
        return HealthVerdict(True, lifecycle, ())
    if snapshot.mergeable_state == "clean":
        return HealthVerdict(True, "queued", ())
    return HealthVerdict(True, "awaiting_checks", ())
