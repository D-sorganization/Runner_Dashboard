"""Ports for delivery-metrics data collection."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from delivery_metrics.models import CheckRunSnapshot, PrSnapshot


class GitHubDeliveryCollectorPort(Protocol):
    """Read PR and check-run facts from GitHub without exposing HTTP details."""

    async def list_pulls_updated_since(
        self,
        repo_full: str,
        since: datetime,
        *,
        max_pages: int = 3,
    ) -> tuple[PrSnapshot, ...]:
        """Return pulls with ``updated_at >= since``, newest first.

        Stops paging early when a page is entirely older than ``since`` or
        ``max_pages`` is reached.
        """

    async def fetch_pull(self, repo_full: str, number: int) -> PrSnapshot:
        """Return one pull by number."""

    async def list_check_runs(self, repo_full: str, head_sha: str) -> tuple[CheckRunSnapshot, ...]:
        """Return all check runs for a commit ref (paginated)."""
