"""Reconcile dashboard state from Repository_Management YAML (role sync).

Holds declared in ``staff/roles/*.yml`` are mirrored into ``staff_holds.json``.
Guardrails removed from YAML are deactivated (never deleted) with an audit trail.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from staff.audit import record_audit
from staff.holds import Hold, HoldsList, _seed_key, seed_from_roles
from staff.roles import RoleSpec, load_roles, roles_yaml_parse_errors

log = logging.getLogger("dashboard.staff.rm_reconcile")

ROLE_SYNC_PRINCIPAL = "role-sync"


class HoldReconcileError(RuntimeError):
    """Roles YAML cannot be trusted; persisted holds must not change."""


@dataclass
class HoldReconcileDiff:
    """Human- and machine-readable outcome of a hold reconcile pass."""

    added: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "added": list(self.added),
            "deactivated": list(self.deactivated),
            "updated": list(self.updated),
            "unchanged": list(self.unchanged),
        }

    @property
    def changed(self) -> bool:
        return bool(self.added or self.deactivated or self.updated)


def reconcile_holds(
    *,
    roles: dict[str, RoleSpec],
    commit_sha: str,
    holds: HoldsList | None = None,
    dry_run: bool = False,
) -> HoldReconcileDiff:
    """Align persisted guardrail holds with ``roles``; leave schedule holds untouched.

    Pre: ``roles`` was loaded from a directory with no YAML syntax errors.
    Post: on success, every YAML hold exists as an active guardrail; YAML guardrails
    absent from roles are inactive. Raises ``HoldReconcileError`` when ``roles``
    cannot be reconciled (caller must fail closed before mutating).
    """
    desired = seed_from_roles(roles)
    desired_by_key = {_seed_key(h.text): h for h in desired}
    desired_keys = frozenset(desired_by_key)

    hl = holds or HoldsList()
    current = hl.load()
    schedule = [h for h in current if h.kind != "guardrail"]
    guardrails = [h for h in current if h.kind == "guardrail"]

    diff = HoldReconcileDiff()
    next_guardrails: list[Hold] = []

    for hold in guardrails:
        key = _seed_key(hold.text)
        if key not in desired_keys:
            if hold.active:
                diff.deactivated.append(hold.text)
                hold.active = False
                if not hold.lifted_when:
                    hold.lifted_when = f"removed from role YAML ({commit_sha[:12]})"
            else:
                diff.unchanged.append(hold.text)
            next_guardrails.append(hold)
            continue
        want = desired_by_key[key]
        changed = False
        if not hold.active:
            hold.active = True
            hold.lifted_when = ""
            changed = True
        if sorted(hold.applies_to) != sorted(want.applies_to):
            hold.applies_to = list(want.applies_to)
            changed = True
        if hold.id != want.id:
            hold.id = want.id
            changed = True
        (diff.updated if changed else diff.unchanged).append(hold.text)
        next_guardrails.append(hold)

    present_keys = {_seed_key(h.text) for h in guardrails}
    for key, want in sorted(desired_by_key.items()):
        if key in present_keys:
            continue
        diff.added.append(want.text)
        next_guardrails.append(want)

    merged = schedule + next_guardrails
    if not dry_run and diff.changed:
        hl.replace([h.to_dict() for h in merged])
        _audit_hold_diff(diff, commit_sha)
    elif dry_run and diff.changed:
        log.info("hold reconcile dry-run commit=%s diff=%s", commit_sha, json.dumps(diff.to_dict(), sort_keys=True))

    return diff


def _audit_hold_diff(diff: HoldReconcileDiff, commit_sha: str) -> None:
    detail_base = {"source_commit": commit_sha, "actor": ROLE_SYNC_PRINCIPAL}
    for text in diff.added:
        record_audit(
            action="hold_set",
            target=f"hold:{text}",
            principal=ROLE_SYNC_PRINCIPAL,
            surface="scheduler",
            outcome="success",
            detail={**detail_base, "text": text, "reconcile": "add"},
        )
    for text in diff.updated:
        record_audit(
            action="hold_set",
            target=f"hold:{text}",
            principal=ROLE_SYNC_PRINCIPAL,
            surface="scheduler",
            outcome="success",
            detail={**detail_base, "text": text, "reconcile": "update"},
        )
    for text in diff.deactivated:
        record_audit(
            action="hold_lift",
            target=f"hold:{text}",
            principal=ROLE_SYNC_PRINCIPAL,
            surface="scheduler",
            outcome="success",
            detail={**detail_base, "text": text, "reconcile": "deactivate"},
        )


def reconcile_holds_for_roles_dir(
    directory: Path | None,
    commit_sha: str,
    *,
    holds: HoldsList | None = None,
    dry_run: bool | None = None,
) -> HoldReconcileDiff | None:
    """Load roles from ``directory`` and reconcile holds; ``None`` when skipped."""
    if directory is None or not directory.is_dir():
        return None
    parse_errors = roles_yaml_parse_errors(directory)
    if parse_errors:
        raise HoldReconcileError("; ".join(parse_errors))
    roles = load_roles(directory)
    is_dry = dry_run if dry_run is not None else os.environ.get("STAFF_RM_RECONCILE_DRY_RUN", "").strip() in {
        "1",
        "true",
        "yes",
    }
    return reconcile_holds(roles=roles, commit_sha=commit_sha, holds=holds, dry_run=is_dry)


def format_hold_reconcile_diff(diff: HoldReconcileDiff) -> str:
    """Printable diff for operators (dry-run CLI)."""
    return json.dumps(diff.to_dict(), indent=2, sort_keys=True)
