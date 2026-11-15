"""Normalized workflow item model shared by external adapters (#4330, epic #4317)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WorkflowOperation(StrEnum):
    """Operations an adapter may support; slice 1 is read-only."""

    LIST = "list"
    GET = "get"


_DEFAULT_OPS = frozenset({WorkflowOperation.LIST, WorkflowOperation.GET})


@dataclass(frozen=True, slots=True)
class WorkflowItem:
    """Provider-neutral work item projection."""

    provider_id: str
    external_id: str
    title: str
    status: str
    url: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    supported_operations: frozenset[WorkflowOperation] = _DEFAULT_OPS
