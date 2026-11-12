"""GitHub enhanced-billing adapter for the Actions-minutes monitor (#2878, slice 2).

The response shapes for ``/organizations/{org}/settings/billing/usage`` and
``/orgs/{org}/settings/billing/budgets`` below follow GitHub's published
enhanced-billing schema. They are unverified against a live response because
the fleet App does not yet hold the billing-read permission grant (owner
action, tracked in #2878's "Permission gap" section) — a 401/403 is the
expected path until that grant exists, and is exactly what this adapter maps
to :class:`BillingPermissionGapError`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import gh_client
from billing_actions.errors import BillingPermissionGapError
from billing_actions.models import BudgetSnapshot, UsageItem
from gh_client import GhServerError

_PERMISSION_DENIED_STATUSES = (401, 403)

_SKU_NAME_MAP: dict[str, str] = {
    "Actions Linux": "UBUNTU",
    "Actions Windows": "WINDOWS",
    "Actions macOS": "MACOS",
}


def _map_sku(raw_sku: str) -> str:
    """Translate GitHub's SKU label to our internal key, or pass it through.

    An unrecognized SKU (e.g. a larger-runner variant) is returned unchanged
    so it is flagged as unknown downstream rather than silently miscounted.
    """
    return _SKU_NAME_MAP.get(raw_sku, raw_sku)


async def _get_or_raise_permission_gap(path: str) -> Any:
    try:
        return await gh_client.get(path)
    except GhServerError as exc:
        if exc.status_code in _PERMISSION_DENIED_STATUSES:
            raise BillingPermissionGapError() from exc
        raise


class GhClientBillingSource:
    """Reads Actions usage and budget caps via :mod:`gh_client`."""

    def __init__(self) -> None:
        self._visibility_cache: dict[str, bool] = {}

    async def fetch_day(self, org: str, day: date) -> list[UsageItem]:
        body = await _get_or_raise_permission_gap(
            f"/organizations/{org}/settings/billing/usage?year={day.year}&month={day.month}&day={day.day}"
        )

        items: list[UsageItem] = []
        for raw in body.get("usageItems", []):
            if str(raw.get("product", "")).lower() != "actions":
                continue
            if str(raw.get("unitType", "")).lower() != "minutes":
                continue
            raw_repo = raw.get("repositoryName")
            if not raw_repo:
                continue
            repo_full = raw_repo if "/" in raw_repo else f"{org}/{raw_repo}"
            items.append(
                UsageItem(
                    date=day,
                    repo_full=repo_full,
                    sku=_map_sku(str(raw.get("sku", ""))),
                    is_public=await self._is_public(repo_full),
                    minutes_used=float(raw.get("quantity", 0.0)),
                )
            )
        return items

    async def fetch_budgets(self, org: str) -> BudgetSnapshot | None:
        body = await _get_or_raise_permission_gap(f"/orgs/{org}/settings/billing/budgets")

        for budget in body.get("budgets", []):
            scope = str(budget.get("scope", "")).lower()
            name = str(budget.get("name", "")).lower()
            if "actions" in scope or "actions" in name:
                return BudgetSnapshot(
                    amount_usd=float(budget.get("amount", 0.0)),
                    prevent_further_usage=bool(budget.get("prevent_further_usage", False)),
                )
        return None

    async def _is_public(self, repo_full: str) -> bool:
        if repo_full not in self._visibility_cache:
            repo = await _get_or_raise_permission_gap(f"/repos/{repo_full}")
            self._visibility_cache[repo_full] = not bool(repo.get("private", True))
        return self._visibility_cache[repo_full]
