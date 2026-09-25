"""Charter coverage: which of a repo's open issues and PRs the charter accounts for.

An open item is *tracked* when a charter feature's Tracking cell names it, or when
its title/body references a tracked number in the same repository (``Part of #N``,
``Fixes #N``) — so epic children and fixing PRs count. Everything else is
*untracked*: the fleet curator's worklist (deprecate, integrate, implement, track).

Pure functions over already-fetched data; the service does the GitHub I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from projects.charter import Feature

UNTRACKED_CAP = 100
_TRACKING_REF = re.compile(r"^(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#(?P<num>\d+)$")
# A bare ``#N`` not preceded by ``owner/repo`` (cross-repo references are ignored).
_BARE_REF = re.compile(r"(?<![\w/.-])#(\d+)\b")


def tracked_numbers(features: Iterable[Feature], repo: str) -> frozenset[int]:
    """Issue/PR numbers in ``repo`` that the charter's Tracking column names."""
    numbers: set[int] = set()
    for feature in features:
        match = _TRACKING_REF.match(feature.tracking.strip())
        if match and (match.group("repo") in (None, repo)):
            numbers.add(int(match.group("num")))
    return frozenset(numbers)


def _references(item: Mapping[str, Any]) -> set[int]:
    text = f"{item.get('title') or ''}\n{item.get('body') or ''}"
    return {int(n) for n in _BARE_REF.findall(text)}


def _summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number": item["number"],
        "title": item.get("title") or "",
        "kind": "pr" if item.get("pull_request") else "issue",
        "url": item.get("html_url") or "",
        "updated_at": item.get("updated_at") or "",
        "labels": [label.get("name", "") for label in item.get("labels") or [] if isinstance(label, dict)],
    }


def classify(repo: str, features: Sequence[Feature], items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Split open items into tracked and untracked. ``untracked`` is capped at ``UNTRACKED_CAP``."""
    tracked = tracked_numbers(features, repo)
    untracked = [
        _summary(item) for item in items if item["number"] not in tracked and not (_references(item) & tracked)
    ]
    total = len(items)
    covered = total - len(untracked)
    return {
        "open_items": total,
        "tracked": covered,
        "percent_tracked": 100 if total == 0 else round(100 * covered / total),
        "untracked_count": len(untracked),
        "untracked": untracked[:UNTRACKED_CAP],
    }
