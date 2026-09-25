"""Data models and schemas for Code Request executor stage (CR-5, issue #1287).

Defines execution states, executor tiers, child issue execution records,
execution configuration, and rollup summaries for multi-wave planned child issues.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExecutorTier(StrEnum):
    """The standard executor tiers (CR-4 / BP-4)."""

    OLLAMA = "ollama"
    CLI = "cli"
    STRONG = "strong"


class ChildExecutionState(StrEnum):
    """Execution lifecycle state of a child issue in a plan."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    PR_OPEN = "pr_open"
    CI = "ci"
    MERGED = "merged"
    FAILED = "failed"
    BLOCKED = "blocked"
    PAUSED_FOR_HUMAN = "paused_for_human"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChildIssuePayload(_Strict):
    """Input payload to register or plan a child issue."""

    key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=1)
    issue_number: int | None = None
    dependencies: list[str] = Field(default_factory=list)
    tier: ExecutorTier = ExecutorTier.OLLAMA
    task_class: str = "feature"
    complexity: str = "routine"
    turnover_doc: str = ""
    file_scope: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class ChildExecutionRecord(BaseModel):
    """Runtime tracking record for one planned child issue."""

    key: str
    repository: str
    title: str
    issue_number: int | None = None
    dependencies: list[str] = Field(default_factory=list)
    tier: ExecutorTier = ExecutorTier.OLLAMA
    task_class: str = "feature"
    complexity: str = "routine"
    state: ChildExecutionState = ChildExecutionState.QUEUED
    agent: str | None = None
    attempts: int = 0
    cost: float = 0.0
    pr_number: int | None = None
    pr_status: str | None = None
    handoff: str = ""
    error_reasons: list[str] = Field(default_factory=list)
    turnover_doc: str = ""
    wave: int = 0
    escalation_history: list[str] = Field(default_factory=list)
    lease_receipt: str | None = None


class ExecutionConfig(_Strict):
    """Tuning and constraints for executor pipeline execution."""

    max_concurrent_per_repo: int = Field(default=3, ge=1, le=20)
    lease_ttl_seconds: int = Field(default=7200, ge=300, le=28800)
    max_tier_attempts: int = Field(default=2, ge=1, le=5)
    auto_advance_waves: bool = True


class ExecutorRollup(BaseModel):
    """Aggregate status rollup for a Code Request plan."""

    code_request_id: str
    state: str  # "queued", "executing", "done", "failed", "paused"
    total_cost: float = 0.0
    waves: list[list[str]] = Field(default_factory=list)
    current_wave: int = 0
    children: dict[str, ChildExecutionRecord] = Field(default_factory=dict)
    completed_children: list[str] = Field(default_factory=list)
    failed_children: list[str] = Field(default_factory=list)
    blocked_children: list[str] = Field(default_factory=list)
    paused_branches: list[str] = Field(default_factory=list)
    audit_log: list[str] = Field(default_factory=list)
