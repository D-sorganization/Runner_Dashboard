"""Delay agent dispatch on queue depth (RD-CI-033, issue #2034).

Throttles new agent dispatch when queue depth or execution backlog is high,
so arrivals match completion without starving user or repair lanes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from time_utils import utc_now_iso

log = logging.getLogger("dashboard.dispatch.queue_delay")

DEFAULT_POLICY_PATH = Path("config/dispatch_limits.json")


@dataclass(frozen=True, slots=True)
class QueueDelayPolicy:
    """Configurable thresholds for queue depth delay."""

    enabled: bool = False
    shadow_mode: bool = True
    max_queued_runs: int = 20
    max_queued_jobs: int = 50
    max_queue_p95_seconds: float = 600.0
    exempt_tiers: tuple[str, ...] = ("user", "repair")
    exempt_dispatch_kinds: tuple[str, ...] = ("ci_fix", "conflict")

    @classmethod
    def default(cls) -> QueueDelayPolicy:
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueDelayPolicy:
        if not isinstance(data, dict):
            raise TypeError("queue_depth_delay configuration must be a dictionary")

        def _as_tuple(val: Any, default: tuple[str, ...]) -> tuple[str, ...]:
            if val is None:
                return default
            return tuple(str(x) for x in val) if isinstance(val, (list, tuple)) else (str(val),)

        return cls(
            enabled=bool(data.get("enabled", False)),
            shadow_mode=bool(data.get("shadow_mode", True)),
            max_queued_runs=int(data.get("max_queued_runs", 20)),
            max_queued_jobs=int(data.get("max_queued_jobs", 50)),
            max_queue_p95_seconds=float(data.get("max_queue_p95_seconds", 600.0)),
            exempt_tiers=_as_tuple(data.get("exempt_tiers"), ("user", "repair")),
            exempt_dispatch_kinds=_as_tuple(data.get("exempt_dispatch_kinds"), ("ci_fix", "conflict")),
        )


@dataclass(frozen=True, slots=True)
class QueueDepthSnapshot:
    """Point-in-time metrics of queue depth and latency."""

    queued_runs: int = 0
    queued_jobs: int = 0
    queue_p95_seconds: float = 0.0
    observed_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True, slots=True)
class DispatchLaneContext:
    """Context of a dispatch attempt determining priority lane and exemptions."""

    tier: str = "agent"
    lane: str = "agent"
    dispatch_kind: str = ""
    principal: str = ""

    def is_exempt(self, policy: QueueDelayPolicy) -> bool:
        """True if the lane, tier, or dispatch kind is exempt from delay."""
        t_norm, l_norm, k_norm = (
            self.tier.strip().lower(),
            self.lane.strip().lower(),
            self.dispatch_kind.strip().lower(),
        )
        if any(t_norm == ex.strip().lower() for ex in policy.exempt_tiers):
            return True
        if any(l_norm == ex.strip().lower() for ex in policy.exempt_tiers):
            return True
        return any(k_norm.startswith(ex.strip().lower()) for ex in policy.exempt_dispatch_kinds)


@dataclass(frozen=True, slots=True)
class QueueDelayVerdict:
    """Verdict from evaluating queue depth delay."""

    allowed: bool
    delayed: bool
    shadow_would_delay: bool
    reason: str
    metrics: QueueDepthSnapshot | None = None


def load_queue_delay_policy(repo_root: Path | None = None, config_path: Path | None = None) -> QueueDelayPolicy:
    """Load queue depth delay policy from config file or return default."""
    root = repo_root or Path.cwd()
    if config_path:
        path = config_path
    elif (root / DEFAULT_POLICY_PATH).is_file():
        path = root / DEFAULT_POLICY_PATH
    elif (root / "dispatch_limits.json").is_file():
        path = root / "dispatch_limits.json"
    else:
        path = root / DEFAULT_POLICY_PATH

    if not path.is_file():
        return QueueDelayPolicy.default()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to parse %s: %s; falling back to default", path, exc)
        return QueueDelayPolicy.default()

    section = raw.get("queue_depth_delay")
    return QueueDelayPolicy.from_dict(section) if isinstance(section, dict) else QueueDelayPolicy.default()


def evaluate_queue_depth_delay(
    ctx: DispatchLaneContext,
    *,
    metrics: QueueDepthSnapshot | None = None,
    policy: QueueDelayPolicy | None = None,
) -> QueueDelayVerdict:
    """Evaluate whether dispatch should proceed or be delayed due to queue depth."""
    pol = policy or QueueDelayPolicy.default()
    snap = metrics or QueueDepthSnapshot()

    if not pol.enabled:
        return QueueDelayVerdict(
            allowed=True, delayed=False, shadow_would_delay=False, reason="queue_delay.enabled=false", metrics=snap
        )

    if ctx.is_exempt(pol):
        return QueueDelayVerdict(
            allowed=True,
            delayed=False,
            shadow_would_delay=False,
            reason=f"exempt lane/tier={ctx.lane or ctx.tier} kind={ctx.dispatch_kind}",
            metrics=snap,
        )

    over_reasons: list[str] = []
    if snap.queued_runs >= pol.max_queued_runs:
        over_reasons.append(f"queued_runs={snap.queued_runs} >= cap={pol.max_queued_runs}")
    if snap.queued_jobs >= pol.max_queued_jobs:
        over_reasons.append(f"queued_jobs={snap.queued_jobs} >= cap={pol.max_queued_jobs}")
    if snap.queue_p95_seconds >= pol.max_queue_p95_seconds:
        over_reasons.append(f"queue_p95_seconds={snap.queue_p95_seconds} >= cap={pol.max_queue_p95_seconds}")

    if not over_reasons:
        return QueueDelayVerdict(
            allowed=True, delayed=False, shadow_would_delay=False, reason="queue depth within thresholds", metrics=snap
        )

    detail = "; ".join(over_reasons)
    if pol.shadow_mode:
        log.warning("SHADOW would-delay agent dispatch: %s", detail)
        return QueueDelayVerdict(
            allowed=True, delayed=False, shadow_would_delay=True, reason=f"shadow_would_delay: {detail}", metrics=snap
        )

    log.info("Delaying agent dispatch on queue depth: %s", detail)
    return QueueDelayVerdict(
        allowed=False, delayed=True, shadow_would_delay=False, reason=f"delayed_queue_depth: {detail}", metrics=snap
    )


def check_dispatch_queue_delay(
    repo_root: Path | None = None,
    *,
    principal: str = "",
    dispatch_kind: str = "",
    is_repair: bool = False,
    tier: str = "agent",
    metrics: QueueDepthSnapshot | None = None,
) -> QueueDelayVerdict:
    """Convenience helper to evaluate queue depth delay from dispatch endpoints."""
    policy = load_queue_delay_policy(repo_root=repo_root)
    eff_tier = "repair" if is_repair else tier
    lane_ctx = DispatchLaneContext(
        tier=eff_tier,
        lane="repair" if is_repair else ("user" if tier == "user" else "agent"),
        dispatch_kind=dispatch_kind,
        principal=principal,
    )
    return evaluate_queue_depth_delay(lane_ctx, metrics=metrics, policy=policy)
