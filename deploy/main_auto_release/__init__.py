"""Automatic main-CI GitHub releases for node auto-deploy (Dieter 2026-10-21)."""

from main_auto_release.gate import commit_passes_main_ci_gate, select_green_commit_with_release
from main_auto_release.naming import (
    AUTO_RELEASE_NOTES_HEADER,
    auto_release_tag_for_commit,
    auto_release_version_for_commit,
    is_auto_release_tag,
)
from main_auto_release.release_index import commit_has_release, index_release_targets

__all__ = [
    "AUTO_RELEASE_NOTES_HEADER",
    "auto_release_tag_for_commit",
    "auto_release_version_for_commit",
    "commit_has_release",
    "commit_passes_main_ci_gate",
    "index_release_targets",
    "is_auto_release_tag",
    "select_green_commit_with_release",
]
