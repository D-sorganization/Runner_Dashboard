"""Normalized GitHub facts for delivery metrics rollups (provider-neutral)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PrSnapshot:
    """One pull request at a point in time.

    Pre: ``repo_full`` contains ``/``; ``number`` is positive.
    Post: ``head_sha`` is non-empty when GitHub returned head metadata.
    """

    repo_full: str
    number: int
    title: str
    state: str
    merged: bool
    draft: bool
    head_sha: str
    base_ref: str
    author_login: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None
    additions: int
    deletions: int
    changed_files: int
    html_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_full": self.repo_full,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "merged": self.merged,
            "draft": self.draft,
            "head_sha": self.head_sha,
            "base_ref": self.base_ref,
            "author_login": self.author_login,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "merged_at": self.merged_at.isoformat() if self.merged_at else None,
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
            "html_url": self.html_url,
        }


@dataclass(frozen=True)
class CheckRunSnapshot:
    """One check run on a commit head."""

    repo_full: str
    head_sha: str
    check_run_id: int
    name: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None
    details_url: str
    app_slug: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_full": self.repo_full,
            "head_sha": self.head_sha,
            "check_run_id": self.check_run_id,
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "details_url": self.details_url,
            "app_slug": self.app_slug,
        }
