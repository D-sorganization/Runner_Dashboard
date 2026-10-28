"""Per-role leader lease with TTL and monotonic fencing (issue #3098, epic #3093 slice 5).

A private role can run on both the Grok Bot host and the RD host; at most
one is leader. Every mutating action carries the leader's fencing token
through :func:`fence_mutation` first (LoD); a follower or stale token is
refused fail-closed (:class:`LeaseDenied`). Self-contained to the
private-roles family -- not the rd-barb single-active-leader feature
(#3117), which this never imports from. On-disk: one JSON file per role,
read-modify-written under an ``fcntl`` lock; never carries decrypted content.

:func:`reconcile_claims` detects split-brain and fails :func:`fence_mutation`
closed until cleared, notifying once via :func:`notify_dieter_relay_stub`
(a seam for the not-yet-wired #3064 relay). Holds are untouched here."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, Literal

try:
    import fcntl  # type: ignore[import-not-found,unused-ignore]
except ImportError:  # pragma: no cover - Windows-only
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("dashboard.private_role_lease")

LEASE_STORE_DIR_ENV = "RD_PRIVATE_ROLE_LEASE_DIR"
MutationAction = Literal["board_post", "thread_reply", "pr_action", "context_write"]
_ACTIONS: frozenset[str] = frozenset({"board_post", "thread_reply", "pr_action", "context_write"})

Reason = str
_REASONS: frozenset[Reason] = frozenset(
    {"held-by-other-host", "not-leader", "stale-token", "lease-missing", "split-brain"}
)


@dataclass(frozen=True)
class PrivateRoleLease:
    """The live lease for one role: who holds it, at what fencing token, until when."""

    role_id: str
    holder: str
    fencing_token: int
    acquired_at: str
    renewed_at: str
    expires_at: str
    ttl_seconds: float


@dataclass(frozen=True)
class LeaseDenied:
    """Visible, non-sensitive fail-closed state. Never treat this as a grant."""

    role_id: str
    reason: Reason

    def __post_init__(self) -> None:
        if self.reason not in _REASONS:
            raise ValueError(f"unknown LeaseDenied reason: {self.reason}")


@dataclass(frozen=True)
class LeaderClaim:
    """One host's self-asserted belief that it holds the lease, used by :func:`reconcile_claims`."""

    role_id: str
    host: str
    fencing_token: int
    asserted_at: str


@dataclass(frozen=True)
class LeaseConflict:
    """A detected split-brain: more than one host believes it is the current leader."""

    role_id: str
    hosts: tuple[str, ...]
    detected_at: str


def default_lease_store_dir() -> Path:
    configured = os.environ.get(LEASE_STORE_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR", "").strip()
    root = Path(base).expanduser() if base else Path("~/.config/runner-dashboard").expanduser()
    return root / "private_role_leases"


def _lease_path(store_dir: Path, role_id: str) -> Path:
    return store_dir / f"{role_id}.json"


def _conflict_path(store_dir: Path, role_id: str) -> Path:
    return store_dir / f"{role_id}.conflict.json"


def _now_iso(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


@contextlib.contextmanager
def _locked_json_file(path: Path) -> Iterator[IO[str]]:
    """Hold an exclusive advisory lock across one read-modify-write (mirrors runner_lease.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with open(path, "r+", encoding="utf-8") as fh:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            if fcntl is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh, fcntl.LOCK_UN)


def _read_json(fh: IO[str]) -> dict[str, Any]:
    fh.seek(0)
    raw = fh.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".tmp-{path.name}-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
            json.dump(data, tmp_fh)
        os.replace(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _lease_from_state(role_id: str, state: dict[str, Any]) -> PrivateRoleLease:
    return PrivateRoleLease(
        role_id=role_id,
        holder=str(state["holder"]),
        fencing_token=int(state["fencing_token"]),
        acquired_at=str(state["acquired_at"]),
        renewed_at=str(state["renewed_at"]),
        expires_at=str(state["expires_at"]),
        ttl_seconds=float(state["ttl_seconds"]),
    )


def _is_active(state: dict[str, Any], now: datetime) -> bool:
    expires_at = state.get("expires_at")
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(str(expires_at)) > now
    except ValueError:
        return False


def _has_conflict(store_dir: Path, role_id: str) -> bool:
    return _conflict_path(store_dir, role_id).exists()


def acquire_or_renew_lease(
    *, role_id: str, host: str, ttl_seconds: float, store_dir: Path | None = None, now: datetime | None = None
) -> PrivateRoleLease | LeaseDenied:
    """Grant/renew/refuse ``role_id``'s lease to ``host`` (pre: ``ttl_seconds`` > 0)."""
    assert ttl_seconds > 0, "ttl_seconds must be positive"
    store_dir = store_dir or default_lease_store_dir()
    now = now or datetime.now(UTC)
    if _has_conflict(store_dir, role_id):
        return LeaseDenied(role_id, "split-brain")
    path = _lease_path(store_dir, role_id)
    with _locked_json_file(path) as fh:
        state = _read_json(fh)
        active = _is_active(state, now)
        if active and str(state.get("holder")) != host:
            return LeaseDenied(role_id, "held-by-other-host")
        now_s = _now_iso(now)
        if active:
            token = int(state["fencing_token"])
            acquired_at = str(state["acquired_at"])
        else:
            token = int(state.get("fencing_token") or 0) + 1
            acquired_at = now_s
        new_state = {
            "holder": host,
            "fencing_token": token,
            "acquired_at": acquired_at,
            "renewed_at": now_s,
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "ttl_seconds": ttl_seconds,
        }
        _atomic_write_json(path, new_state)
        return _lease_from_state(role_id, new_state)


def revoke_lease(*, role_id: str, host: str, store_dir: Path | None = None, now: datetime | None = None) -> None:
    """Release ``role_id``'s lease if ``host`` holds it (else a no-op)."""
    store_dir = store_dir or default_lease_store_dir()
    now = now or datetime.now(UTC)
    path = _lease_path(store_dir, role_id)
    with _locked_json_file(path) as fh:
        state = _read_json(fh)
        if str(state.get("holder")) != host:
            return
        state["expires_at"] = _now_iso(now)
        _atomic_write_json(path, state)


def fence_mutation(
    *,
    role_id: str,
    host: str,
    fencing_token: int,
    action: MutationAction,
    store_dir: Path | None = None,
    now: datetime | None = None,
) -> PrivateRoleLease | LeaseDenied:
    """The one boundary every mutating action passes through first (LoD); fails closed."""
    assert action in _ACTIONS, f"unknown mutation action: {action!r}"
    store_dir = store_dir or default_lease_store_dir()
    now = now or datetime.now(UTC)
    if _has_conflict(store_dir, role_id):
        return LeaseDenied(role_id, "split-brain")
    path = _lease_path(store_dir, role_id)
    with _locked_json_file(path) as fh:
        state = _read_json(fh)
        if not state or not _is_active(state, now):
            return LeaseDenied(role_id, "lease-missing")
        if int(state.get("fencing_token", -1)) != fencing_token:
            return LeaseDenied(role_id, "stale-token")
        if str(state.get("holder")) != host:
            return LeaseDenied(role_id, "not-leader")
        return _lease_from_state(role_id, state)


def complete_write_in_progress(
    *,
    role_id: str,
    claimed_token: int,
    idempotent: bool,
    store_dir: Path | None = None,
    now: datetime | None = None,
) -> Literal["completed", "rolled-back"]:
    """Revocation-during-write: current-token writes complete; a moved-past token
    completes only if ``idempotent``, else rolls back."""
    store_dir = store_dir or default_lease_store_dir()
    now = now or datetime.now(UTC)
    path = _lease_path(store_dir, role_id)
    with _locked_json_file(path) as fh:
        state = _read_json(fh)
    current_token = int(state.get("fencing_token", -1)) if state else -1
    if current_token == claimed_token:
        return "completed"
    return "completed" if idempotent else "rolled-back"


def reconcile_claims(
    *,
    role_id: str,
    claims: Sequence[LeaderClaim],
    store_dir: Path | None = None,
    now: datetime | None = None,
    notify: Callable[[LeaseConflict], None] | None = None,
) -> LeaseConflict | None:
    """Detect split-brain across self-asserted leader ``claims`` for ``role_id``; notifies
    once per new conflict, ``None`` for <= 1 distinct host."""
    assert all(c.role_id == role_id for c in claims), "all claims must name the same role_id"
    store_dir = store_dir or default_lease_store_dir()
    now = now or datetime.now(UTC)
    hosts = tuple(sorted({c.host for c in claims}))
    if len(hosts) < 2:
        return None
    conflict = LeaseConflict(role_id=role_id, hosts=hosts, detected_at=_now_iso(now))
    marker_path = _conflict_path(store_dir, role_id)
    already_open = marker_path.exists()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(
        marker_path, {"role_id": conflict.role_id, "hosts": list(conflict.hosts), "detected_at": conflict.detected_at}
    )
    if not already_open:
        (notify or notify_dieter_relay_stub)(conflict)
    return conflict


def clear_split_brain(*, role_id: str, store_dir: Path | None = None) -> None:
    """Drop the conflict marker so mutations can resume; does not decide who leads next."""
    store_dir = store_dir or default_lease_store_dir()
    _conflict_path(store_dir, role_id).unlink(missing_ok=True)


def notify_dieter_relay_stub(conflict: LeaseConflict) -> None:
    """Seam for surfacing a split-brain conflict to Dieter via the not-yet-wired #3064
    relay. Logs one category-only line (role id, host count -- never content or keys).
    TODO(#3064): send a ``scripts.rd_threads_envelope`` alert instead, drop this stub."""
    log.warning(
        "private-role-lease split-brain: role=%s conflicting_hosts=%d",
        conflict.role_id,
        len(conflict.hosts),
    )
