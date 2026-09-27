"""Node-local persistence for executor pipelines (WP-2.2, #1606).

One JSON file next to the Code Request store keeps each pipeline's snapshot, keyed by
Code Request id, so a dashboard restart resumes execution where it stopped.
"""

from __future__ import annotations

from pathlib import Path

from code_requests.executor_models import ChildExecutionRecord, ExecutionConfig
from code_requests.keyed_json_store import KeyedJsonStore
from pydantic import BaseModel, Field


class PipelineSnapshot(BaseModel):
    """Everything an :class:`~code_requests.executor_stage.ExecutorPipeline` needs to resume."""

    code_request_id: str
    session_id: str = "executor-pipeline"
    config: ExecutionConfig = Field(default_factory=ExecutionConfig)
    children: dict[str, ChildExecutionRecord] = Field(default_factory=dict)
    waves: list[list[str]] = Field(default_factory=list)
    current_wave_idx: int = Field(default=0, ge=0)
    paused_branches: list[str] = Field(default_factory=list)
    audit_log: list[str] = Field(default_factory=list)


class PipelineStore(KeyedJsonStore[PipelineSnapshot]):
    """JSON-file store of :class:`PipelineSnapshot` keyed by Code Request id."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, PipelineSnapshot, lambda s: s.code_request_id)
