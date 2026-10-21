"""Load and reconcile operator directives from Repository_Management YAML."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from priorities.directives import Directive, DirectivesList
from staff.audit import record_audit
from staff.rm_reconcile import ROLE_SYNC_PRINCIPAL

log = logging.getLogger("dashboard.staff.rm_directives")

DIRECTIVES_RELATIVE = ("staff", "directives.yml")


class DirectivesReconcileError(RuntimeError):
    """``staff/directives.yml`` cannot be parsed; persisted directives must not change."""


@dataclass
class DirectivesReconcileDiff:
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


def directives_yaml_path(rm_root: Path) -> Path:
    return rm_root.joinpath(*DIRECTIVES_RELATIVE)


def load_directives_yaml(path: Path) -> list[dict[str, Any]]:
    """Parse ``staff/directives.yml``; missing file is an empty list."""
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DirectivesReconcileError(f"{path.name}: {exc}") from exc
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = raw.get("directives", [])
    elif isinstance(raw, list):
        items = raw
    else:
        raise DirectivesReconcileError(f"{path.name}: top level must be a mapping or list")
    if not isinstance(items, list):
        raise DirectivesReconcileError(f"{path.name}: directives must be a list")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise DirectivesReconcileError(f"{path.name}: directive #{index} is not a mapping")
        out.append(item)
    return out


def _directive_from_yaml(item: dict[str, Any], now: datetime) -> Directive:
    directive = Directive.from_dict({**item, "managed": True})
    if directive.expires and not directive.is_active(now):
        directive.active = False
    return directive


def reconcile_directives(
    yaml_items: list[dict[str, Any]],
    commit_sha: str,
    *,
    directives: DirectivesList | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> DirectivesReconcileDiff:
    """Merge RM YAML directives into the persisted list; deactivate removed managed rows."""
    moment = now or datetime.now(UTC)
    desired = [_directive_from_yaml(item, moment) for item in yaml_items]
    desired_by_id = {d.id: d for d in desired}

    dl = directives or DirectivesList()
    stored = dl.load()
    diff = DirectivesReconcileDiff()
    next_rows: list[Directive] = []

    for row in stored:
        if not row.managed:
            next_rows.append(row)
            continue
        want = desired_by_id.get(row.id)
        if want is None:
            if row.active:
                row.active = False
                diff.deactivated.append(row.text)
            else:
                diff.unchanged.append(row.text)
            next_rows.append(row)
            continue
        changed = False
        for field_name in ("text", "repo", "priority", "expires", "set_by"):
            if getattr(row, field_name) != getattr(want, field_name):
                setattr(row, field_name, getattr(want, field_name))
                changed = True
        if row.active != want.active:
            row.active = want.active
            changed = True
        (diff.updated if changed else diff.unchanged).append(row.text)
        next_rows.append(row)

    present = {d.id for d in stored if d.managed}
    for directive in desired:
        if directive.id in present:
            continue
        diff.added.append(directive.text)
        next_rows.append(directive)

    if not dry_run and diff.changed:
        dl.replace_all(next_rows)
        _audit_directive_diff(diff, commit_sha)
    return diff


def _audit_directive_diff(diff: DirectivesReconcileDiff, commit_sha: str) -> None:
    detail_base = {"source_commit": commit_sha, "actor": ROLE_SYNC_PRINCIPAL}
    for text in diff.added + diff.updated:
        record_audit(
            action="maintenance",
            target="directives",
            principal=ROLE_SYNC_PRINCIPAL,
            surface="scheduler",
            outcome="success",
            detail={**detail_base, "text": text, "reconcile": "upsert"},
        )
    for text in diff.deactivated:
        record_audit(
            action="maintenance",
            target="directives",
            principal=ROLE_SYNC_PRINCIPAL,
            surface="scheduler",
            outcome="success",
            detail={**detail_base, "text": text, "reconcile": "deactivate"},
        )


def reconcile_directives_for_rm_root(
    rm_root: Path,
    commit_sha: str,
    *,
    directives: DirectivesList | None = None,
    dry_run: bool = False,
) -> DirectivesReconcileDiff:
    path = directives_yaml_path(rm_root)
    items = load_directives_yaml(path)
    return reconcile_directives(items, commit_sha, directives=directives, dry_run=dry_run)
