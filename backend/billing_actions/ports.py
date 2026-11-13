"""Ports for the Actions-minutes billing monitor (#2878, slice 2)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from billing_actions.models import BudgetSnapshot, UsageItem


class BillingUsageSource(Protocol):
    """Read Actions usage and budget caps from GitHub's billing API."""

    async def fetch_day(self, org: str, day: date) -> list[UsageItem]:
        """Return Actions usage items for one UTC day, across repos and SKUs.

        Raises :class:`billing_actions.errors.BillingPermissionGapError` if
        the caller lacks the billing-read permission.
        """

    async def fetch_budgets(self, org: str) -> BudgetSnapshot | None:
        """Return the org's Actions budget cap, or ``None`` if none is set.

        Raises :class:`billing_actions.errors.BillingPermissionGapError` if
        the caller lacks the billing-read permission.
        """
