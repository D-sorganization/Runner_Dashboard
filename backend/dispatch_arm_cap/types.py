"""Typed policy and PR snapshot models for armed-PR WIP cap (RD-CI-005)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ArmTier = Literal["user", "repair", "agent"]
ReservationStatus = Literal["pending", "confirmed", "released", "expired"]
Lifecycle = Literal["awaiting_checks", "queued", "building", "evicted", "unarmed"]

CONFLICT_DISARMED_LABEL = "conflict:disarmed"
DEFAULT_POLICY_PATH = Path("config/dispatch_limits.json")
DEFAULT_STORE_PATH = Path.home() / "actions-runners" / "dashboard" / "dispatch_arm_cap.sqlite3"


def fallback_temp_store_path() -> Path:
    """Fallback writable state path in temp directory for sandboxed runs (#2254)."""
    tmp_base = os.environ.get("TMPDIR") or tempfile.gettempdir()
    return Path(tmp_base) / "runner-dashboard" / "dispatch_arm_cap.sqlite3"


def resolve_store_path(preferred_path: Path | None = None) -> Path:
    """Resolve a writable path for dispatch_arm_cap.sqlite3 (#2254).

    Priority:
    1. preferred_path if given
    2. RUNNER_DASHBOARD_STATE_DIR env var if set
    3. DEFAULT_STORE_PATH (~/actions-runners/dashboard/dispatch_arm_cap.sqlite3)
    """
    if preferred_path is not None:
        return preferred_path

    env_dir = os.environ.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if env_dir:
        return Path(env_dir) / "dispatch_arm_cap.sqlite3"

    import sys

    arm_cap_mod = sys.modules.get("dispatch_arm_cap")
    if arm_cap_mod is not None and hasattr(arm_cap_mod, "DEFAULT_STORE_PATH"):
        return arm_cap_mod.DEFAULT_STORE_PATH
    return DEFAULT_STORE_PATH


@dataclass(frozen=True)
class ArmCapPolicy:
    enabled: bool
    shadow_mode: bool
    max_healthy_armed: int
    reservation_ttl_seconds: int
    reconcile_interval_seconds: int
    scope_repositories: tuple[str, ...] | None
    required_checks_path: Path
    fallback_checks_path: Path
    disarm_workflow: str
    disarm_transient_streak_limit: int
    disarm_cooldown_seconds: int
    repo_root: Path

    @classmethod
    def from_dict(cls, data: dict[str, Any], repo_root: Path) -> ArmCapPolicy:
        if not isinstance(data, dict):
            raise ValueError("dispatch_limits policy must be a dict")
        scope = data.get("scope_repositories")
        scope_tuple: tuple[str, ...] | None
        if scope is None:
            scope_tuple = None
        elif isinstance(scope, list):
            scope_tuple = tuple(str(r) for r in scope)
        else:
            raise ValueError("scope_repositories must be null or a list of repo slugs")

        def _resolve(path_value: str) -> Path:
            path = Path(path_value)
            return path if path.is_absolute() else repo_root / path

        return cls(
            enabled=bool(data.get("enabled", False)),
            shadow_mode=bool(data.get("shadow_mode", True)),
            max_healthy_armed=int(data.get("max_healthy_armed", 12)),
            reservation_ttl_seconds=int(data.get("reservation_ttl_seconds", 300)),
            reconcile_interval_seconds=int(data.get("reconcile_interval_seconds", 900)),
            scope_repositories=scope_tuple,
            required_checks_path=_resolve(str(data.get("required_checks_path", "config/ci-required-checks-live.json"))),
            fallback_checks_path=_resolve(
                str(data.get("fallback_checks_path", "config/required_status_checks_policy.json"))
            ),
            disarm_workflow=str(data.get("disarm_workflow", "conflict-disarm.yml")),
            disarm_transient_streak_limit=int(data.get("disarm_transient_streak_limit", 2)),
            disarm_cooldown_seconds=int(data.get("disarm_cooldown_seconds", 3600)),
            repo_root=repo_root,
        )


@dataclass(frozen=True)
class PrArmSnapshot:
    repo: str
    number: int
    armed: bool
    draft: bool
    mergeable_state: str
    labels: tuple[str, ...]
    in_merge_queue: bool
    merge_queue_state: str
    check_runs: tuple[tuple[str, str], ...]

    @classmethod
    def from_github(cls, repo: str, raw: dict[str, Any], checks: list[tuple[str, str]] | None = None) -> PrArmSnapshot:
        labels = tuple(str(x) for x in raw.get("labels", []) if isinstance(x, str))
        if not labels and isinstance(raw.get("labels"), list):
            labels = tuple(str(item.get("name", "")) for item in raw.get("labels", []) if isinstance(item, dict))
        mq = raw.get("merge_queue_entry") or {}
        mq_state = str(mq.get("state") or "") if isinstance(mq, dict) else ""
        return cls(
            repo=repo,
            number=int(raw.get("number") or 0),
            armed=raw.get("auto_merge") is not None,
            draft=bool(raw.get("draft")),
            mergeable_state=str(raw.get("mergeable_state") or "").lower(),
            labels=labels,
            in_merge_queue=bool(mq_state),
            merge_queue_state=mq_state.lower(),
            check_runs=tuple(checks or ()),
        )


@dataclass(frozen=True)
class HealthVerdict:
    healthy: bool
    lifecycle: Lifecycle
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AdmitContext:
    tier: ArmTier = "agent"
    dispatch_kind: str = ""

    def bypass_cap(self) -> bool:
        if self.tier == "user":
            return True
        kind = self.dispatch_kind.strip().lower()
        if self.tier == "repair":
            return kind.startswith("ci_fix") or kind.startswith("conflict")
        return False


@dataclass(frozen=True)
class AdmitDecision:
    allowed: bool
    shadow_would_block: bool = False
    reservation_token: str | None = None
    reason: str = ""
    healthy_armed: int = 0
    cap: int = 0
