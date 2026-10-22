"""Versioned fleet coding standards for staff dispatch (#2157 slice 2)."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from staff.workspace import rm_root

_STANDARDS_ENV = "STAFF_STANDARDS_PATH"
_REL = ("staff", "standards", "FLEET.md")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class FleetStandards:
    version: str
    body: str
    content_hash: str
    source_path: str

    @property
    def pin(self) -> str:
        return f"fleet-standards@{self.version}+{self.content_hash[:12]}"


_BUILTIN = FleetStandards(
    version="1.0.0",
    body=(
        "Mandatory fleet coding standards (cannot be omitted; extend only with dispatch-specific notes):\n"
        "- TDD: write a failing test first, then minimal code, then refactor; no PR without meaningful tests.\n"
        "- DbC: validate at boundaries; reject invalid dispatch inputs with clear errors.\n"
        "- LoD: talk to immediate collaborators only; no deep object graphs in handlers.\n"
        "- DRY: one source of truth; reuse registry skills and standards instead of copying prose.\n"
        "- Tests use real failure data; no vacuous assertions or placeholder-only coverage.\n"
        "- PRs stay under 500 lines; split sequential PRs against main when larger.\n"
        "- Open ready PRs only (never drafts); arm squash auto-merge; no human-approval gates.\n"
        "- Ship change fragments and PR Handoff sections; respect schedule holds and Barb blocks.\n"
    ),
    content_hash=hashlib.sha256(b"fleet-standards-v1-builtin").hexdigest(),
    source_path="<builtin>",
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
    version = str(meta.get("version") or "").strip()
    if not version or not _VERSION_RE.match(version):
        raise ValueError("standards version must be semver")
    body = parts[2].lstrip("\n")
    content_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    return FleetStandards(version=version, body=body, content_hash=content_hash, source_path=source_path)


def standards_path() -> Path | None:
    override = os.environ.get(_STANDARDS_ENV)
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    root = rm_root()
    if root is not None:
        cand = root.joinpath(*_REL)
        if cand.is_file():
            return cand
    return None


def load_fleet_standards() -> FleetStandards:
    """Post: always returns a standards package (RM file or built-in v1.0.0)."""
    path = standards_path()
    if path is None:
        return _BUILTIN
    try:
        return _parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, ValueError):
        return _BUILTIN
