"""Per-repository drain status derived from operator directives (#3915).

A repository is drained while an active, repo-scoped directive whose text starts
with "drain mode" exists and its open (non-draft) PR count is at or above the
threshold (``DRAIN_OPEN_PR_THRESHOLD``, default 20). The drain lifts by itself
when the directive expires (``DirectivesList.active`` drops it) or the count
falls below the threshold. Fleet-wide (``*``) directives never drain a repo:
a drain is a per-repo brake. An unknown count keeps the drain on; only the
node-side guard fails open, and only when the hub itself is unreachable.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from priorities.directives import _BARE_REPO, Directive

DEFAULT_THRESHOLD = 20
THRESHOLD_ENV = "DRAIN_OPEN_PR_THRESHOLD"
_DRAIN_TEXT = re.compile(r"^\s*drain[\s_-]*mode\b", re.IGNORECASE)


def is_drain_directive(directive: Directive) -> bool:
    return bool(_DRAIN_TEXT.match(directive.text))


def threshold_from_env() -> int:
    raw = os.environ.get(THRESHOLD_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_THRESHOLD
    return value if value > 0 else DEFAULT_THRESHOLD


def validate_repo(repo: str) -> str:
    """DbC: a bare repository name (no owner, no path), else ``ValueError``."""
    if not _BARE_REPO.match(repo or "") or repo in (".", ".."):
        raise ValueError("repo must be a bare repository name (no owner, no path)")
    return repo


def active_drain_directive(repo: str, directives: Iterable[Directive], now: datetime) -> Directive | None:
    for directive in directives:
        if directive.repo == repo and directive.is_active(now) and is_drain_directive(directive):
            return directive
    return None


def drain_status(
    repo: str,
    directives: Iterable[Directive],
    *,
    open_pr_count: int | None,
    now: datetime,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """``{repo, drained, reason, directive_id, expires, open_pr_count, threshold}`` for one repo."""
    validate_repo(repo)
    directive = active_drain_directive(repo, directives, now)
    status: dict[str, Any] = {
        "repo": repo,
        "drained": False,
        "reason": "no_directive",
        "directive_id": directive.id if directive else None,
        "expires": directive.expires if directive else "",
        "open_pr_count": open_pr_count,
        "threshold": threshold,
    }
    if directive is None:
        return status
    if open_pr_count is None:
        status.update(drained=True, reason="count_unavailable")
    elif open_pr_count >= threshold:
        status.update(drained=True, reason="active")
    else:
        status["reason"] = "below_threshold"
    return status
