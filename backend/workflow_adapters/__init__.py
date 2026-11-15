"""Workflow-system adapter seam (Notion, Linear, Obsidian, Slack) — #4330."""

from workflow_adapters.contract import WorkflowSystemAdapter, assert_adapter_contract
from workflow_adapters.fake import FakeWorkflowAdapter
from workflow_adapters.models import WorkflowItem, WorkflowOperation

__all__ = [
    "FakeWorkflowAdapter",
    "WorkflowItem",
    "WorkflowOperation",
    "WorkflowSystemAdapter",
    "assert_adapter_contract",
]