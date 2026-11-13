"""Operator drain-mode directive evaluation and gh ``pr create`` guard rules (#3915)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from priorities.directives import Directive

DEFAULT_DRAIN_OPEN_PR_THRESHOLD = 20
_DRAIN_MODE_TEXT = re.compile(r"drain\s+mode", re.IGNORECASE)
_EXEMPT_LABELS = frozenset({"ci", "security", "red-main", "ci-unblock", "runner-unblock", "runner"})
_EXEMPT_TITLE_PREFIXES = (
    "ci:",
    "fix(ci):",
    "chore(ci):",
    "security:",
    "fix(security):",
    "red-main:",
    "fix(red-main):",
    "runner:",
    "fix(runner):",
)


def is_drain_mode_directive(text: str) -> bool:
    return bool(_DRAIN_MODE_TEXT.search(text or ""))


def directive_targets_repo(directive: Directive, bare_repo: str) -> bool:
    scope = (directive.repo or "*").casefold()
    return scope == "*" or scope == bare_repo.casefold()


def pick_drain_directive(
    directives: Sequence[Directive],
    bare_repo: str,
    *,
    now: datetime | None = None,
) -> Directive | None:
    moment = now or datetime.now(UTC)
    matches = [
        d
        for d in directives
        if d.is_active(moment) and is_drain_mode_directive(d.text) and directive_targets_repo(d, bare_repo)
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda d: (d.priority, d.set_on))[0]


def evaluate_repo_drain_gate(
    *,
    open_pr_count: int,
    directive: Directive | None,
    threshold: int = DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    expires: str | None = directive.expires or None if directive else None
    if directive is None or not directive.is_active(moment):
        return {
            "drained": False,
            "expires": expires,
            "open_pr_count": open_pr_count,
            "threshold": threshold,
        }
    drained = open_pr_count >= threshold and directive.is_active(moment)
    return {
        "drained": drained,
        "expires": expires,
        "open_pr_count": open_pr_count,
        "threshold": threshold,
    }


def classify_pr_create_exemption(*, title: str, labels: Sequence[str] | None = None) -> bool:
    lowered_title = (title or "").strip().casefold()
    if any(lowered_title.startswith(prefix) for prefix in _EXEMPT_TITLE_PREFIXES):
        return True
    for raw in labels or ():
        token = str(raw).strip().casefold()
        if token in _EXEMPT_LABELS:
            return True
        if token.startswith("agent:") and token.removeprefix("agent:") in _EXEMPT_LABELS:
            return True
    if "red-main" in lowered_title or "red main" in lowered_title:
        return True
    return False


def should_block_pr_create(*, drained: bool, exempt: bool) -> bool:
    return bool(drained and not exempt)


def normalize_repo_query(repo: str) -> tuple[str, str]:
    """Return ``(bare_repo, owner/repo)`` for fleet org repos."""
    text = str(repo).strip().lstrip("/")
    if not text or ".." in text:
        raise ValueError("repo must be a repository name, not a path")
    if "/" in text:
        owner, _, name = text.partition("/")
        if not owner or not name:
            raise ValueError("repo must be owner/name or a bare repository name")
        return name, f"{owner}/{name}"
    return text, f"D-sorganization/{text}"
