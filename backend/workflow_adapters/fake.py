"""In-memory adapter for contract tests (#4330)."""

from __future__ import annotations

from collections.abc import Iterable

from workflow_adapters.models import WorkflowItem


class FakeWorkflowAdapter:
    """Deterministic fake provider used only in tests and CI."""

    provider_id = "fake"

    def __init__(self, *, items: Iterable[WorkflowItem]) -> None:
        self._by_id = {item.external_id: item for item in items}

    def list_items(self) -> list[WorkflowItem]:
        return list(self._by_id.values())

    def get_item(self, external_id: str) -> WorkflowItem | None:
        return self._by_id.get(external_id)