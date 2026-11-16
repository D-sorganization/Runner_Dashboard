"""Contract tests for the workflow-system adapter seam (#4330)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from workflow_adapters.contract import assert_adapter_contract, WorkflowSystemAdapter
from workflow_adapters.fake import FakeWorkflowAdapter
from workflow_adapters.models import WorkflowItem, WorkflowOperation


def test_fake_adapter_satisfies_contract() -> None:
    adapter = FakeWorkflowAdapter(
        items=[
            WorkflowItem(
                provider_id="fake",
                external_id="item-1",
                title="First",
                status="open",
                url="https://example.test/items/1",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
                supported_operations=frozenset({WorkflowOperation.LIST, WorkflowOperation.GET}),
            )
        ]
    )
    assert isinstance(adapter, WorkflowSystemAdapter)
    assert_adapter_contract(adapter)


def test_incomplete_adapter_fails_contract() -> None:
    class BadAdapter:
        provider_id = "bad"

        def list_items(self) -> list[WorkflowItem]:
            return []

    assert not isinstance(BadAdapter(), WorkflowSystemAdapter)

    with pytest.raises(TypeError):
        assert_adapter_contract(BadAdapter())  # type: ignore[arg-type]


def test_get_item_returns_none_for_missing_id() -> None:
    adapter = FakeWorkflowAdapter(items=[])
    assert_adapter_contract(adapter)
    assert adapter.get_item("missing") is None