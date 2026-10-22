"""Retention policy for automatic main CI releases."""

from __future__ import annotations

from typing import Any

from main_auto_release.naming import is_auto_release_tag


def _target_sha(release: dict[str, Any]) -> str:
    return str(release.get("target_commitish") or "").lower()


def select_releases_to_delete(
    releases: list[dict[str, Any]],
    *,
    keep_count: int,
    protected_shas: set[str],
) -> list[dict[str, Any]]:
    """Return auto-release rows to delete, never including ``protected_shas``."""
    if keep_count < 1:
        raise ValueError("keep_count must be at least 1")

    protected = {s.lower() for s in protected_shas if s}
    auto_rows: list[dict[str, Any]] = []
    for row in releases:
        if not isinstance(row, dict) or row.get("draft"):
            continue
        tag = str(row.get("tag_name") or "")
        if not is_auto_release_tag(tag):
            continue
        auto_rows.append(row)

    if len(auto_rows) <= keep_count:
        return []

    survivors = auto_rows[:keep_count]
    survivor_shas = {_target_sha(r) for r in survivors}
    deletable: list[dict[str, Any]] = []
    for row in auto_rows[keep_count:]:
        sha = _target_sha(row)
        if sha in protected or sha[:7] in protected:
            continue
        if sha in survivor_shas:
            continue
        deletable.append(row)
    return deletable
