"""Map published GitHub releases to target commit SHAs."""

from __future__ import annotations

from typing import Any

from main_auto_release.naming import is_auto_release_tag


def re_semver_tag(tag_name: str) -> bool:
    body = tag_name.strip()
    if not body.startswith("v"):
        return False
    rest = body[1:]
    parts = rest.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def _normalize_sha(value: str) -> str | None:
    sha = value.lower().strip()
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    if len(sha) == 7 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return None


def index_release_targets(releases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pre: GitHub ``/releases`` list items. Post: map full or short sha -> newest release row."""
    out: dict[str, dict[str, Any]] = {}
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        target = str(release.get("target_commitish") or "").lower()
        sha = _normalize_sha(target)
        if sha is None:
            continue
        tag = str(release.get("tag_name") or "")
        if not is_auto_release_tag(tag) and not re_semver_tag(tag):
            continue
        out[sha] = release
        if len(sha) == 40:
            out[sha[:7]] = release
    return out


def commit_has_release(releases: list[dict[str, Any]], commit_sha: str) -> bool:
    sha = _normalize_sha(commit_sha)
    if sha is None:
        return False
    index = index_release_targets(releases)
    if sha in index:
        return True
    if len(sha) == 40 and sha[:7] in index:
        return True
    return False
