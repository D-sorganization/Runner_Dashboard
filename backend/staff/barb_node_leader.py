"""rd-barb node-leader lease + fencing primitive (#3117 slice S1, part of #3064).

One active rd-barb leader at a time, a configured warm standby, and every other
node a forwarder that never leads. This reuses the existing scoped-lease fence
(:mod:`staff.barb_lease_fence`, :mod:`staff.barb_continuity`) under a dedicated
scope rather than inventing a second lease mechanism (DRY): any ``barb:<x>``
scope is already a valid lease scope, so the node-leader role needs no schema
change there, only its own config and a thin, node-shaped wrapper.

Deferred to a later slice, pending an explicit decision on amending
``docs/adr/0008-barb-single-authority.md`` (which currently makes failover
manual by binding owner ruling): automatic promotion on a missed heartbeat, the
per-node health forwarder, and cross-node alert dedup. This slice only makes
"exactly one active leader" and "a stale leader's writes are rejected" true;
it does not decide who calls ``assume_leadership`` or when.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from staff.barb_continuity import DEFAULT_LEASE_TTL_SECONDS, BarbContinuityStore
from staff.barb_lease_fence import BarbClaim

NODE_LEADER_SCOPE = "barb:node-leader"
_VALID_ROLES = ("leader", "standby", "forwarder")
_ROLE_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
DEFAULT_NODE_ROLES_PATH = Path(__file__).resolve().parents[2] / "config" / "rd_barb_node_roles.json"


def _normalize_persona_role(role: str = "rd-barb") -> str:
    raw = str(role or "").strip()
    if not _ROLE_SLUG_RE.match(raw):
        raise ValueError(f"invalid persona role {role!r}; expected lowercase slug matching ^[a-z][a-z0-9-]*$")
    return "rd-barb" if raw == "barb" else raw


def leader_scope_for(role: str = "rd-barb") -> str:
    """Return the node-leader lease scope for ``role`` ('barb:node-leader' for rd-barb)."""
    norm = _normalize_persona_role(role)
    return NODE_LEADER_SCOPE if norm == "rd-barb" else f"{NODE_LEADER_SCOPE}:{norm}"


class NodeRoleConfigError(RuntimeError):
    """The node-roles config is missing, malformed, or violates an invariant; callers fail closed."""


@dataclass(frozen=True)
class NodeRoleEntry:
    name: str
    role: str
    priority: int = 0


@dataclass(frozen=True)
class NodeLeaderConfig:
    """Pre: exactly one ``leader``, at most one ``standby``, unique names, known roles."""

    nodes: tuple[NodeRoleEntry, ...]

    def leader_name(self) -> str:
        return next(n.name for n in self.nodes if n.role == "leader")

    def standby_names(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.nodes if n.role == "standby")

    def role_of(self, name: str) -> str | None:
        return next((n.role for n in self.nodes if n.name == name), None)


def _validated_entries(data: object) -> tuple[NodeRoleEntry, ...]:
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or not nodes:
        raise NodeRoleConfigError("node-roles config needs a non-empty 'nodes' list")
    entries: list[NodeRoleEntry] = []
    seen: set[str] = set()
    leaders = standbys = 0
    for raw in nodes:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            raise NodeRoleConfigError("every node entry needs a name")
        name, role = str(raw["name"]).strip(), str(raw.get("role") or "").strip()
        if role not in _VALID_ROLES:
            raise NodeRoleConfigError(f"node {name!r} has unknown role {role!r}; expected one of {_VALID_ROLES}")
        if name in seen:
            raise NodeRoleConfigError(f"duplicate node name {name!r}")
        seen.add(name)
        leaders += role == "leader"
        standbys += role == "standby"
        entries.append(NodeRoleEntry(name=name, role=role, priority=int(raw.get("priority") or 0)))
    if leaders != 1:
        raise NodeRoleConfigError(f"node-roles config needs exactly one 'leader', found {leaders}")
    if standbys > 1:
        raise NodeRoleConfigError(f"node-roles config allows at most one 'standby', found {standbys}")
    return tuple(entries)


def load_node_leader_config(path: Path = DEFAULT_NODE_ROLES_PATH) -> NodeLeaderConfig:
    """Read the node-roles config through a read-only open. Raises :class:`NodeRoleConfigError`."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise NodeRoleConfigError(f"cannot read node-roles config: {exc}") from exc
    return NodeLeaderConfig(nodes=_validated_entries(data))


def claim_for(node: str, epoch: int, *, role: str = "rd-barb") -> BarbClaim:
    """The :class:`BarbClaim` a node presents to act as the current node-leader at ``epoch``."""
    return BarbClaim(scope=leader_scope_for(role), epoch=epoch, caller_id=node)


def assume_leadership(
    store: BarbContinuityStore,
    *,
    node: str,
    role: str = "rd-barb",
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Acquire or renew the node-leader lease for ``node``; a live other holder conflicts unless ``force``."""
    norm = _normalize_persona_role(role)
    return store.checkout_lease(
        scope=leader_scope_for(norm),
        holder=node,
        machine=node,
        persona=norm,
        ttl_seconds=ttl_seconds,
        force=force,
        now=now,
    )


def renew_leadership(
    store: BarbContinuityStore,
    *,
    node: str,
    epoch: int,
    role: str = "rd-barb",
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Extend ``node``'s live node-leader lease at ``epoch`` (no epoch bump)."""
    return store.heartbeat_lease(
        scope=leader_scope_for(role), holder=node, epoch=epoch, ttl_seconds=ttl_seconds, now=now
    )


def release_leadership(
    store: BarbContinuityStore, *, node: str, role: str = "rd-barb", now: datetime | None = None
) -> dict[str, Any]:
    """Release ``node``'s node-leader lease; no shift-turnover note required for a machine role."""
    return store.release_lease(scope=leader_scope_for(role), holder=node, require_turnover=False, now=now)


def current_leader(
    store: BarbContinuityStore, *, role: str = "rd-barb", now: datetime | None = None
) -> dict[str, Any] | None:
    """The active node-leader lease, or ``None`` when no node currently holds it."""
    target = leader_scope_for(role)
    return next((lease for lease in store.list_leases(now=now) if lease["scope"] == target), None)
