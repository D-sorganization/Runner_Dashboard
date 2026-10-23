"""Versioned fleet coding standards for staff dispatch (#2157 slice 2, #2220)."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_STANDARDS_ENV = "STAFF_STANDARDS_PATH"
_REL = ("staff", "standards", "FLEET.md")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class FleetStandards:
    version: str
    body: str
    content_hash: str
    source_path: str
    name: str = "fleet-standards"
    source_revision: str = "HEAD"
    fallback: bool = False

    @property
    def digest(self) -> str:
        return self.content_hash

    @property
    def pin(self) -> str:
        return f"{self.name}@{self.version}+{self.content_hash[:12]}"


_BUILTIN = FleetStandards(
    version="1.0.0",
    body=(
        "Mandatory fleet coding standards (cannot be omitted; extend only with dispatch-specific notes):\n"
        "- TDD: write a failing test first, then minimal code, then refactor; no PR without meaningful tests.\n"
        "- DbC: validate at boundaries; reject invalid dispatch inputs with clear errors.\n"
        "- LoD: talk to immediate collaborators only; no deep object graphs in handlers.\n"
        "- DRY: one source of truth; reuse registry skills and standards instead of copying prose.\n"
        "- Tests use real failure data; no vacuous assertions or placeholder-only coverage.\n"
        "- PRs stay under 500 lines; split sequential PRs against main when larger; never stack PRs.\n"
        "- Open ready PRs only (never drafts); arm squash auto-merge and end session "
        "(pr_lifecycle: arm_and_exit); no human reviewers.\n"
        "- Never merge, never force-push, never touch other worktrees or host configuration.\n"
        "- Never take work labelled claim:local or under another agent's live lease.\n"
        "- Skip an issue if an open pull request already references it; never file bulk remediation issues.\n"
        "- Ship change fragments and PR Handoff sections; respect schedule holds and Barb blocks.\n"
    ),
    content_hash=hashlib.sha256(b"fleet-standards-v1-builtin").hexdigest(),
    source_path="<builtin>",
    source_revision="builtin",
    fallback=True,
)


def _parse(text: str, source_path: str) -> FleetStandards:
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        raise ValueError("standards file must begin with YAML frontmatter")
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        raise ValueError("standards frontmatter is not closed")
    meta = yaml.safe_load(parts[1])
    if not isinstance(meta, dict):
        raise ValueError("standards frontmatter must be a mapping")
    name = str(meta.get("name") or "fleet-standards").strip()
    version = str(meta.get("version") or "").strip()
    if not version or not _VERSION_RE.match(version):
        raise ValueError("standards version must be semver")
    body = parts[2].lstrip("\n")
    if not body.strip():
        raise ValueError("standards body cannot be empty")
    digest = str(meta.get("digest") or "").strip() or hashlib.sha256(body.encode("utf-8")).hexdigest()
    source_revision = str(meta.get("source_revision") or "HEAD").strip()
    return FleetStandards(
        version=version,
        body=body,
        content_hash=digest,
        source_path=source_path,
        name=name,
        source_revision=source_revision,
        fallback=(source_path == "<builtin>"),
    )


def standards_path() -> Path | None:
    override = os.environ.get(_STANDARDS_ENV)
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    from staff.workspace import rm_root

    root = rm_root()
    if root is not None:
        cand = root.joinpath(*_REL)
        if cand.is_file():
            return cand
    return None


def load_fleet_standards() -> FleetStandards:
    """Post: always returns a standards package with non-empty body and semver version."""
    path = standards_path()
    if path is None:
        standards = _BUILTIN
    else:
        try:
            standards = _parse(path.read_text(encoding="utf-8"), str(path))
        except (OSError, ValueError):
            standards = _BUILTIN

    assert standards.body.strip(), "DbC: standards body must be non-empty"
    assert _VERSION_RE.match(standards.version), f"DbC: standards version must be semver: {standards.version}"
    return standards
