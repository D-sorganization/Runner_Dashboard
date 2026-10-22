"""Deterministic tag and artifact version strings for main CI auto-releases."""

from __future__ import annotations

import re
from datetime import UTC, datetime

AUTO_RELEASE_NOTES_HEADER = "Auto-generated main CI release (main-ci-auto-release.yml)"

_AUTO_TAG_RE = re.compile(r"^v(20\d{2})\.(\d{2})\.(\d{2})-([0-9a-f]{7})$")


def _commit_day_utc(committer_iso8601: str) -> datetime:
    """Pre: GitHub commit.committer.date (UTC Z). Post: timezone-aware datetime."""
    normalized = committer_iso8601.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def auto_release_tag_for_commit(full_sha: str, committer_date: str) -> str:
    """Return ``vYYYY.MM.DD-<shortsha>`` (unique per commit, stable across re-runs)."""
    sha = full_sha.lower()
    if len(sha) != 40:
        raise ValueError("full_sha must be a 40-character git object id")
    when = _commit_day_utc(committer_date)
    short = sha[:7]
    return f"v{when.year}.{when.month:02d}.{when.day:02d}-{short}"


def auto_release_version_for_commit(full_sha: str, committer_date: str) -> str:
    """Artifact basename version (no leading ``v``)."""
    return auto_release_tag_for_commit(full_sha, committer_date)[1:]


def is_auto_release_tag(tag_name: str) -> bool:
    return bool(_AUTO_TAG_RE.match(tag_name.strip()))
