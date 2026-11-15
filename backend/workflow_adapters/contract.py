"""Workflow adapter protocol and contract harness (#4330)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from workflow_adapters.models import WorkflowItem, WorkflowOperation


@runtime_checkable
class WorkflowSystemAdapter(Protocol):
    """Minimal read-only adapter surface; network/auth left to implementations."""

    provider_id: str

    def list_items(self) -> list[WorkflowItem]:
        """All items visible to this adapter (bounded by the implementation)."""

    def get_item(self, external_id: str) -> WorkflowItem | None:
        """One item by provider id, or ``None`` when absent."""


def assert_adapter_contract(adapter: WorkflowSystemAdapter) -> None:
    """Runtime checks every concrete or fake adapter must satisfy."""
    if not isinstance(adapter, WorkflowSystemAdapter):
        raise TypeError(f"{type(adapter).__name__} does not implement WorkflowSystemAdapter")
    provider_id = adapter.provider_id
    if not provider_id or not isinstance(provider_id, str):
        raise AssertionError("provider_id must be a non-empty str")

    listed = adapter.list_items()
    if not isinstance(listed, list):
        raise AssertionError("list_items must return a list")
    for item in listed:
        if not isinstance(item, WorkflowItem):
            raise AssertionError("list_items must return WorkflowItem instances")
        if item.provider_id != provider_id:
            raise AssertionError("WorkflowItem.provider_id must match adapter.provider_id")
        if WorkflowOperation.LIST not in item.supported_operations:
            raise AssertionError("listed items must advertise WorkflowOperation.LIST")
        if adapter.get_item(item.external_id) != item:
            raise AssertionError("get_item must return the same object as list_items for known ids")

    missing = adapter.get_item("__contract_missing_external_id__")
    if missing is not None:
        raise AssertionError("get_item must return None for unknown external_id")