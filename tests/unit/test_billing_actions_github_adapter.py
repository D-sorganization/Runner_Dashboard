"""GhClientBillingSource behaviour (#2878, slice 2)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import gh_client
import pytest
from billing_actions.adapters.github import GhClientBillingSource
from billing_actions.errors import BillingPermissionGapError
from gh_client import GhServerError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "billing_actions"
ORG = "D-sorganization"


@pytest.fixture
def source() -> GhClientBillingSource:
    return GhClientBillingSource()


def _usage_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "usage_day.json").read_text(encoding="utf-8"))


def _budgets_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "budgets.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_fetch_day_parses_actions_minutes_and_resolves_visibility(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    usage = _usage_fixture()
    repo_visibility = {
        f"{ORG}/private-repo": {"private": True},
        f"{ORG}/public-repo": {"private": False},
    }

    async def fake_get(path: str) -> Any:
        if path.startswith("/organizations/"):
            return usage
        for repo_full, payload in repo_visibility.items():
            if path == f"/repos/{repo_full}":
                return payload
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(gh_client, "get", fake_get)

    items = await source.fetch_day(ORG, date(2026, 10, 1))

    assert len(items) == 2
    private_item = next(i for i in items if i.repo_full.endswith("private-repo"))
    public_item = next(i for i in items if i.repo_full.endswith("public-repo"))
    assert private_item.is_public is False
    assert private_item.sku == "UBUNTU"
    assert private_item.minutes_used == 120.0
    assert private_item.date == date(2026, 10, 1)
    assert public_item.is_public is True


@pytest.mark.asyncio
async def test_fetch_day_ignores_non_actions_and_non_minutes_items(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    usage = {
        "usageItems": [
            {
                "product": "Packages",
                "unitType": "GigabyteDays",
                "sku": "Packages Storage",
                "repositoryName": f"{ORG}/private-repo",
                "quantity": 5,
            },
            *_usage_fixture()["usageItems"],
        ]
    }

    async def fake_get(path: str) -> Any:
        if path.startswith("/organizations/"):
            return usage
        return {"private": True}

    monkeypatch.setattr(gh_client, "get", fake_get)

    items = await source.fetch_day(ORG, date(2026, 10, 1))
    assert len(items) == 2
    assert all(item.sku != "Packages Storage" for item in items)


@pytest.mark.asyncio
async def test_fetch_day_unmapped_sku_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    usage = {
        "usageItems": [
            {
                "product": "Actions",
                "unitType": "minutes",
                "sku": "Actions Linux 16-core",
                "repositoryName": f"{ORG}/private-repo",
                "quantity": 30,
            }
        ]
    }

    async def fake_get(path: str) -> Any:
        if path.startswith("/organizations/"):
            return usage
        return {"private": True}

    monkeypatch.setattr(gh_client, "get", fake_get)

    items = await source.fetch_day(ORG, date(2026, 10, 1))
    assert items[0].sku == "Actions Linux 16-core"


@pytest.mark.asyncio
async def test_fetch_day_403_maps_to_permission_gap(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    async def fake_get(path: str) -> Any:
        raise GhServerError(403, path, "Resource not accessible by integration")

    monkeypatch.setattr(gh_client, "get", fake_get)

    with pytest.raises(BillingPermissionGapError):
        await source.fetch_day(ORG, date(2026, 10, 1))


@pytest.mark.asyncio
async def test_fetch_day_401_maps_to_permission_gap(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    async def fake_get(path: str) -> Any:
        raise GhServerError(401, path, "Bad credentials")

    monkeypatch.setattr(gh_client, "get", fake_get)

    with pytest.raises(BillingPermissionGapError):
        await source.fetch_day(ORG, date(2026, 10, 1))


@pytest.mark.asyncio
async def test_fetch_day_other_status_propagates(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    async def fake_get(path: str) -> Any:
        raise GhServerError(500, path, "boom")

    monkeypatch.setattr(gh_client, "get", fake_get)

    with pytest.raises(GhServerError):
        await source.fetch_day(ORG, date(2026, 10, 1))


@pytest.mark.asyncio
async def test_fetch_budgets_finds_actions_scoped_budget(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    async def fake_get(path: str) -> Any:
        return _budgets_fixture()

    monkeypatch.setattr(gh_client, "get", fake_get)

    snapshot = await source.fetch_budgets(ORG)
    assert snapshot is not None
    assert snapshot.amount_usd == 500.0
    assert snapshot.prevent_further_usage is True


@pytest.mark.asyncio
async def test_fetch_budgets_returns_none_when_no_actions_budget(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    async def fake_get(path: str) -> Any:
        return {
            "budgets": [
                {
                    "name": "Codespaces cap",
                    "scope": "codespaces",
                    "amount": 10,
                    "prevent_further_usage": False,
                }
            ]
        }

    monkeypatch.setattr(gh_client, "get", fake_get)

    snapshot = await source.fetch_budgets(ORG)
    assert snapshot is None


@pytest.mark.asyncio
async def test_fetch_budgets_403_maps_to_permission_gap(
    monkeypatch: pytest.MonkeyPatch, source: GhClientBillingSource
) -> None:
    async def fake_get(path: str) -> Any:
        raise GhServerError(403, path, "Resource not accessible by integration")

    monkeypatch.setattr(gh_client, "get", fake_get)

    with pytest.raises(BillingPermissionGapError):
        await source.fetch_budgets(ORG)
