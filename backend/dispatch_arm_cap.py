"""Atomic WIP cap on healthy armed pull requests (RD-CI-005, issue #2029).

Reservations are taken in the RD store before ``automerge_guard.py --arm`` runs.
Reconciliation keeps inventory aligned with live ``auto_merge`` state, including
arms made outside Runner Dashboard. Shadow mode logs would-block / would-disarm
without refusing arms until operators flip ``shadow_mode`` to false.

Preconditions: policy file validates; store path is writable.
Postconditions: at most one pending reservation per (repo, pr); confirmed arms
are counted in inventory; expired pending reservations never count toward the cap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from time_utils import utc_now_iso

logger = logging.getLogger("dispatch_arm_cap")

CONFLICT_DISARMED_LABEL = "conflict:disarmed"
DEFAULT_POLICY_PATH = Path("config/dispatch_limits.json")
DEFAULT_STORE_PATH = Path.home() / "actions-runners" / "dashboard" / "dispatch_arm_cap.sqlite3"

ArmTier = Literal["user", "repair", "agent"]
ReservationStatus = Literal["pending", "confirmed", "released", "expired"]
Lifecycle = Literal["awaiting_checks", "queued", "building", "evicted", "unarmed"]

_FAILING_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required"})


def _parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


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
            # ci-required-checks-live.json shape: per-repo map with contexts list
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
        if snapshot.merge_queue_state in {"merging", "merged"}:
            return HealthVerdict(True, "building", ())
        return HealthVerdict(True, "queued", ())
    if snapshot.mergeable_state == "clean":
        return HealthVerdict(True, "queued", ())
    return HealthVerdict(True, "awaiting_checks", ())


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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS arm_reservations (
    token TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    status TEXT NOT NULL,
    tier TEXT NOT NULL,
    dispatch_kind TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_arm_res_repo_pr_pending
    ON arm_reservations(repo, pr) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS armed_inventory (
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    healthy INTEGER NOT NULL,
    lifecycle TEXT NOT NULL,
    reasons TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repo, pr)
);
CREATE TABLE IF NOT EXISTS cap_disarm_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ArmCapStore:
    """SQLite-backed reservations and reconciled armed inventory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def expire_stale_reservations(self, now_iso: str | None = None) -> int:
        now = now_iso or utc_now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE arm_reservations SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
                (now,),
            )
            return int(cur.rowcount)

    def count_healthy_armed(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM armed_inventory WHERE healthy = 1").fetchone()
            return int(row["c"] if row else 0)

    def count_pending_reservations(self, exclude_token: str | None = None) -> int:
        with self._lock, self._connect() as conn:
            if exclude_token:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending' AND token != ?",
                    (exclude_token,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending'").fetchone()
            return int(row["c"] if row else 0)

    def effective_healthy_load(self, exclude_token: str | None = None) -> int:
        return self.count_healthy_armed() + self.count_pending_reservations(exclude_token)

    def upsert_inventory(
        self,
        repo: str,
        pr: int,
        verdict: HealthVerdict,
        *,
        updated_at: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO armed_inventory (repo, pr, healthy, lifecycle, reasons, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, pr) DO UPDATE SET "
                "healthy=excluded.healthy, lifecycle=excluded.lifecycle, "
                "reasons=excluded.reasons, updated_at=excluded.updated_at",
                (
                    repo,
                    pr,
                    1 if verdict.healthy else 0,
                    verdict.lifecycle,
                    json.dumps(list(verdict.reasons)),
                    updated_at or utc_now_iso(),
                ),
            )

    def remove_inventory(self, repo: str, pr: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM armed_inventory WHERE repo = ? AND pr = ?", (repo, pr))

    def try_reserve(
        self,
        repo: str,
        pr: int,
        ctx: AdmitContext,
        *,
        cap: int,
        ttl_seconds: int,
        now_iso: str | None = None,
    ) -> AdmitDecision:
        """Atomically reserve a cap slot if load < cap (single-flight per repo#pr)."""
        now = now_iso or utc_now_iso()
        expires = (_parse_iso(now) + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        token = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_in_conn(conn, now)
            pending_same = conn.execute(
                "SELECT token FROM arm_reservations WHERE repo = ? AND pr = ? AND status = 'pending'",
                (repo, pr),
            ).fetchone()
            if pending_same:
                return AdmitDecision(
                    allowed=True,
                    reservation_token=str(pending_same["token"]),
                    reason="existing pending reservation",
                )
            load = self._effective_load_in_conn(conn)
            if load >= cap:
                return AdmitDecision(
                    allowed=False,
                    reason=f"healthy armed cap reached ({load}/{cap})",
                    healthy_armed=load,
                    cap=cap,
                )
            try:
                conn.execute(
                    "INSERT INTO arm_reservations "
                    "(token, repo, pr, status, tier, dispatch_kind, reserved_at, expires_at) "
                    "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
                    (token, repo, pr, ctx.tier, ctx.dispatch_kind, now, expires),
                )
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                return AdmitDecision(allowed=False, reason="reservation race lost")
            conn.execute("COMMIT")
            return AdmitDecision(allowed=True, reservation_token=token, healthy_armed=load + 1, cap=cap)

    def confirm_reservation(self, token: str, *, now_iso: str | None = None) -> bool:
        now = now_iso or utc_now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE arm_reservations SET status = 'confirmed', confirmed_at = ? "
                "WHERE token = ? AND status = 'pending'",
                (now, token),
            )
            return cur.rowcount == 1

    def release_reservation(self, token: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE arm_reservations SET status = 'released' WHERE token = ? AND status = 'pending'",
                (token,),
            )

    def recover_pending_as_inventory_gap(self) -> int:
        """After restart, pending reservations still occupy cap until expired or confirmed."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending'").fetchone()
            return int(row["c"] if row else 0)

    def get_disarm_state(self, key: str) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM cap_disarm_state WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else ""

    def set_disarm_state(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO cap_disarm_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @staticmethod
    def _expire_in_conn(conn: sqlite3.Connection, now_iso: str) -> None:
        conn.execute(
            "UPDATE arm_reservations SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
            (now_iso,),
        )

    @staticmethod
    def _effective_load_in_conn(conn: sqlite3.Connection) -> int:
        inv = conn.execute("SELECT COUNT(*) AS c FROM armed_inventory WHERE healthy = 1").fetchone()
        pending = conn.execute("SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending'").fetchone()
        return int(inv["c"] if inv else 0) + int(pending["c"] if pending else 0)


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
    logger.info(
        "Refusing arm on %s#%s: %s",
        repo,
        pr,
        decision.reason,
    )
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
    seen: set[tuple[str, int]] = set()
    for snap in snapshots:
        if policy.scope_repositories and snap.repo not in policy.scope_repositories:
            continue
        seen.add((snap.repo, snap.number))
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
    """True when enforce mode is on, load exceeds cap, and cooldown allows disarm."""
    if not policy.enabled or policy.shadow_mode:
        return False
    if healthy_count <= policy.max_healthy_armed:
        return False
    now = _parse_iso(now_iso or utc_now_iso())
    last = store.get_disarm_state("last_disarm_at")
    if last:
        if now - _parse_iso(last) < timedelta(seconds=policy.disarm_cooldown_seconds):
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
    """Inputs for RD #2026 conflict-disarm caller (RM #2205 reusable workflow)."""
    return {
        "dry_run": "true" if dry_run else "false",
        "exclude_prs": ",".join(str(n) for n in (exclude_prs or [])),
    }
