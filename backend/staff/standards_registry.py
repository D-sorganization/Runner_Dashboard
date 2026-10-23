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

ESTIMATOR_VERSION = "chars_div_4_v1"

# Aggregate prompt context budget limits (tokens)
TIER0_TOKEN_CAP = 1500
TIER1_TOKEN_CAP = 1000
LESSONS_TOKEN_CAP = 600
TOTAL_CONTEXT_TOKEN_CAP = 2200


class ContextBudgetExceededError(ValueError):
    """Raised when an injected context block or total context exceeds its token cap."""


def estimate_tokens(text: str) -> int:
    """Standard fleet token estimator: (len(text) + 3) // 4 (chars / 4)."""
    if not text:
        return 0
    return (len(text) + 3) // 4


@dataclass(frozen=True)
class FleetStandards:
    version: str
    body: str
    content_hash: str
    source_path: str
    digest: str = ""
    source_revision: str = ""
    name: str = "fleet-standards"

    def __post_init__(self) -> None:
        if not self.digest:
            object.__setattr__(self, "digest", self.content_hash)
        if not self.version or not _VERSION_RE.match(self.version):
            raise ValueError(f"standards version {self.version!r} must be semver")
        if not self.body.strip():
            raise ValueError("standards body must not be empty")

    @property
    def pin(self) -> str:
        return f"{self.name}@{self.version}+{self.content_hash[:12]}"

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.body)


_RESULT_CONTRACT = (
    "Output contract: the very last line of your final message must start with 'STAFF_RESULT:' followed by a "
    "one-sentence summary. A run that ends without that line is recorded as failed, even when the work is done."
)

_BUILTIN = FleetStandards(
    version="1.0.0",
    name="fleet-standards",
    body=(
        "Mandatory fleet coding standards (cannot be omitted; extend only with dispatch-specific notes):\n"
        "- TDD, DbC, LoD, DRY: write a failing test first; validate boundaries; no deep object graphs; "
        "one source of truth.\n"
        "- work only inside this worktree; commit with Conventional Commits; push once.\n"
        "- open ready PRs only (never drafts); arm auto-merge via automerge_guard and end session "
        "(pr_lifecycle: arm_and_exit); no human-approval gates; never request human reviewers.\n"
        "- PRs stay under 500 lines (deletion-only PRs may exceed 500); no stacked PRs.\n"
        "- never merge, never force-push, never touch other worktrees or host configuration.\n"
        "- never take an issue or PR labelled claim:local or under another agent's live lease; "
        "skip if an open pull request already references it.\n"
        "- never file bulk remediation issues; report findings in one issue or PR.\n"
        "- on Windows, gh is the fleet App wrapper (d-sorggeneralagent[bot]); gh-personal only for owner-run actions.\n"
        "- subscriptions only; never use pay-per-use API keys.\n"
        "- ship change fragments; include the PR URL, if you opened one, in your STAFF_RESULT summary.\n"
        f"- {_RESULT_CONTRACT}"
    ),
    content_hash=hashlib.sha256(b"fleet-standards-v1-builtin").hexdigest(),
    source_path="<builtin>",
    digest=hashlib.sha256(b"fleet-standards-v1-builtin").hexdigest(),
    source_revision="builtin",
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
        raise ValueError(f"standards version {version!r} must be semver")
    name = str(meta.get("name") or "fleet-standards").strip()
    digest = str(meta.get("digest") or "").strip()
    source_revision = str(meta.get("source_revision") or "").strip()
    body = parts[2].lstrip("\n")
    if not body.strip():
        raise ValueError("standards body must not be empty")
    content_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    if not digest:
        digest = content_hash
    return FleetStandards(
        version=version,
        body=body,
        content_hash=content_hash,
        source_path=source_path,
        digest=digest,
        source_revision=source_revision,
        name=name,
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
    """Postcondition: always returns a valid standards package with semver version and non-empty body."""
    path = standards_path()
    if path is None:
        return _BUILTIN
    try:
        res = _parse(path.read_text(encoding="utf-8"), str(path))
        if not res.body.strip() or not _VERSION_RE.match(res.version):
            return _BUILTIN
        return res
    except (OSError, ValueError):
        return _BUILTIN
