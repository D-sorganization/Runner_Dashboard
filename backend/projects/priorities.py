"""Fleet project priority tiers (the owner's baseline ranking of what to focus on).

Source of truth: Repository_Management ``config/project_priorities.yaml`` on its
default branch, read through the same GitHub file fetch as the charters. The
owner sets tiers (the priority interview); stewards and the fleet curator read
them. This module only parses and orders — it never writes.

File contract (``schema_version: 1``)::

    projects:
      <repo>:
        tier: P0 | P1 | P2 | P3 | P4        # required
        focus: <one line, optional>
        rationale: <one line, optional>
        decided: YYYY-MM-DD                  # optional

A repository absent from the file is ``unranked``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import yaml

TIERS: tuple[str, ...] = ("P0", "P1", "P2", "P3", "P4")
UNRANKED = "unranked"
TIER_MEANING: dict[str, str] = {
    "P0": "Critical focus: the fleet's top objective right now",
    "P1": "High: actively developed every week",
    "P2": "Normal: steady progress when P0/P1 allow",
    "P3": "Low: opportunistic work only",
    "P4": "Maintenance or sunset: keep green, no new features",
}
PRIORITIES_REPO = "Repository_Management"
PRIORITIES_PATH = "config/project_priorities.yaml"
SCHEMA_VERSION = 1
_REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_OPTIONAL = ("focus", "rationale", "decided")


class PriorityError(ValueError):
    """The priority file violates its contract."""


@dataclass(frozen=True)
class ProjectPriority:
    tier: str
    focus: str = ""
    rationale: str = ""
    decided: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def unranked() -> ProjectPriority:
    return ProjectPriority(tier=UNRANKED)


def tier_rank(tier: str) -> int:
    """Sort key: P0 first, anything unknown (including ``unranked``) last."""
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


def _entry(repo: str, raw: Any) -> ProjectPriority:
    if not _REPO_NAME.match(repo) or ".." in repo:
        raise PriorityError(f"invalid repository name {repo!r}")
    if not isinstance(raw, dict):
        raise PriorityError(f"{repo}: entry must be a mapping")
    tier = raw.get("tier")
    if tier not in TIERS:
        raise PriorityError(f"{repo}: invalid tier {tier!r}; expected one of {', '.join(TIERS)}")
    extras = {key: "" if raw.get(key) is None else str(raw[key]).strip() for key in _OPTIONAL}
    return ProjectPriority(tier=tier, **extras)


def parse_priorities(text: str) -> dict[str, ProjectPriority]:
    """Parse the priority file. Raises ``PriorityError`` on any contract violation."""
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PriorityError(f"YAML: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise PriorityError(f"schema_version must be {SCHEMA_VERSION}")
    projects = payload.get("projects")
    projects = {} if projects is None else projects
    if not isinstance(projects, dict):
        raise PriorityError("projects must be a mapping of repository name to entry")
    return {str(repo): _entry(str(repo), raw) for repo, raw in projects.items()}
