"""Policy load, arm admission, inventory reconcile, and disarm workflow inputs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from dispatch_arm_cap.health import classify_arm_health, load_required_contexts
from dispatch_arm_cap.store import ArmCapStore
from dispatch_arm_cap.types import (
    DEFAULT_POLICY_PATH,
    AdmitContext,
    AdmitDecision,
    ArmCapPolicy,
    PrArmSnapshot,
)
from time_utils import utc_now_iso

logger = logging.getLogger("dispatch_arm_cap.admission")


def _parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def load_policy(repo_root: Path | None = None, policy_path: Path | None = None) -> ArmCapPolicy:
    root = repo_root or Path.cwd()
    path = policy_path or (root / DEFAULT_POLICY_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read dispatch limits policy at {path}: {exc}") from exc
    return ArmCapPolicy.from_dict(data, root)


def admit_for_arm(
    repo: str,
    pr: int,
    ctx: AdmitContext,
    *,
    store: ArmCapStore,
    policy: ArmCapPolicy,
) -> AdmitDecision:
    """Gate ``--arm`` with cap policy; shadow mode never refuses."""
    if not policy.enabled:
        return AdmitDecision(allowed=True, reason="dispatch_limits.enabled=false")
    if policy.scope_repositories and repo not in policy.scope_repositories:
        return AdmitDecision(allowed=True, reason="repo out of scope")
    if ctx.bypass_cap():
        return AdmitDecision(allowed=True, reason=f"bypass tier={ctx.tier} kind={ctx.dispatch_kind}")

    store.expire_stale_reservations()
    decision = store.try_reserve(
        repo,
        pr,
        ctx,
        cap=policy.max_healthy_armed,
        ttl_seconds=policy.reservation_ttl_seconds,
    )
    if decision.allowed:
        return decision
    if policy.shadow_mode:
        logger.warning(
            "SHADOW would-block arm on %s#%s: %s (healthy=%s cap=%s)",
            repo,
            pr,
            decision.reason,
            decision.healthy_armed,
            decision.cap,
        )
        return AdmitDecision(
            allowed=True,
            shadow_would_block=True,
            reason=decision.reason,
            healthy_armed=decision.healthy_armed,
            cap=decision.cap,
        )
    logger.info("Refusing arm on %s#%s: %s", repo, pr, decision.reason)
    return decision


def reconcile_snapshots(
    snapshots: list[PrArmSnapshot],
    *,
    store: ArmCapStore,
    policy: ArmCapPolicy,
) -> dict[str, int]:
    """Refresh inventory from pre-fetched PR snapshots (tests and reconcile job)."""
    required = load_required_contexts(policy)
    counts = {"healthy": 0, "unhealthy": 0, "unarmed": 0}
    for snap in snapshots:
        if policy.scope_repositories and snap.repo not in policy.scope_repositories:
            continue
        verdict = classify_arm_health(snap, required)
        if not snap.armed:
            store.remove_inventory(snap.repo, snap.number)
            counts["unarmed"] += 1
            continue
        store.upsert_inventory(snap.repo, snap.number, verdict)
        if verdict.healthy:
            counts["healthy"] += 1
        else:
            counts["unhealthy"] += 1
    return counts


def should_trigger_cap_disarm(
    healthy_count: int,
    policy: ArmCapPolicy,
    store: ArmCapStore,
    *,
    now_iso: str | None = None,
) -> bool:
    if not policy.enabled or policy.shadow_mode:
        return False
    if healthy_count <= policy.max_healthy_armed:
        return False
    now = _parse_iso(now_iso or utc_now_iso())
    last = store.get_disarm_state("last_disarm_at")
    if last and now - _parse_iso(last) < timedelta(seconds=policy.disarm_cooldown_seconds):
        return False
    streak = int(store.get_disarm_state("over_cap_streak") or "0")
    if streak < policy.disarm_transient_streak_limit:
        store.set_disarm_state("over_cap_streak", str(streak + 1))
        return False
    return True


def record_disarm_triggered(store: ArmCapStore, *, now_iso: str | None = None) -> None:
    store.set_disarm_state("last_disarm_at", now_iso or utc_now_iso())
    store.set_disarm_state("over_cap_streak", "0")


def build_disarm_workflow_inputs(
    policy: ArmCapPolicy,
    *,
    dry_run: bool,
    exclude_prs: list[int] | None = None,
) -> dict[str, str]:
    return {
        "dry_run": "true" if dry_run else "false",
        "exclude_prs": ",".join(str(n) for n in (exclude_prs or [])),
    }
