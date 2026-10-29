"""Immutable content-hashed role versions, rollback, pinned task versions (RD #3104).

Each publish creates an immutable ``RoleVersion``. Rollback appends a new
version with content equal to a chosen prior one; history is never
rewritten. ``dispatch_task`` is a self-contained stand-in for the real
task/lease boundary (#3098, not built yet): it pins the version current at
dispatch so a later publish never changes an in-flight task.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field

from staff.bot_builder.schema import BuilderRoleDefinition


def _canonical_role_payload(role: BuilderRoleDefinition) -> dict[str, object]:
    return {
        "schema_version": role.schema_version,
        "name": role.name,
        "persona": role.persona,
        "instructions": role.instructions,
        "skills": list(role.skills),
        "model": role.model,
        "providers": list(role.providers),
        "schedule": role.schedule,
        "triggers": [{"target_role": t.target_role, "event": t.event} for t in role.triggers],
        "host": role.host,
        "enabled": role.enabled,
        "capabilities": list(role.capabilities),
        "private_context_ref": role.private_context_ref,
        "household_data_opt_in": role.household_data_opt_in,
    }


def compute_content_hash(role: BuilderRoleDefinition) -> str:
    """DbC: role is already parsed; hash is a pure function of its content."""
    payload = json.dumps(_canonical_role_payload(role), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RoleVersion:
    version_id: str
    content_hash: str
    role: BuilderRoleDefinition
    sequence: int
    rolled_back_from: str | None = None


class RoleVersionLedger:
    """Append-only, per-role version history. History is never rewritten."""

    def __init__(self, role_id: str) -> None:
        self.role_id = role_id
        self._history: list[RoleVersion] = []
        self._seq = itertools.count(start=1)

    @property
    def history(self) -> tuple[RoleVersion, ...]:
        return tuple(self._history)

    @property
    def current(self) -> RoleVersion | None:
        return self._history[-1] if self._history else None

    @property
    def original_role(self) -> BuilderRoleDefinition | None:
        return self._history[0].role if self._history else None

    def _append(self, role: BuilderRoleDefinition, content_hash: str, rolled_back_from: str | None) -> RoleVersion:
        seq = next(self._seq)
        version = RoleVersion(
            version_id=f"v{seq}-{content_hash[:12]}",
            content_hash=content_hash,
            role=role,
            sequence=seq,
            rolled_back_from=rolled_back_from,
        )
        self._history.append(version)
        return version

    def publish(self, role: BuilderRoleDefinition) -> RoleVersion:
        """DbC: caller has already validated ``role``."""
        return self._append(role, compute_content_hash(role), rolled_back_from=None)

    def get(self, version_id: str) -> RoleVersion:
        for version in self._history:
            if version.version_id == version_id:
                return version
        raise KeyError(f"unknown version id: {version_id!r}")

    def rollback(self, target_version_id: str) -> RoleVersion:
        """Append a new version equal in content to ``target_version_id``; history is preserved."""
        target = self.get(target_version_id)
        return self._append(target.role, target.content_hash, rolled_back_from=target.version_id)


@dataclass(frozen=True)
class TaskHandle:
    """Self-contained task/dispatch stand-in that pins a version at dispatch time."""

    role_id: str
    pinned_version_id: str
    pinned_content_hash: str
    pinned_role: BuilderRoleDefinition = field(repr=False)


def dispatch_task(ledger: RoleVersionLedger) -> TaskHandle:
    """DbC: ledger must have at least one published version."""
    current = ledger.current
    if current is None:
        raise ValueError("cannot dispatch a task against a role with no published version")
    return TaskHandle(
        role_id=ledger.role_id,
        pinned_version_id=current.version_id,
        pinned_content_hash=current.content_hash,
        pinned_role=current.role,
    )
