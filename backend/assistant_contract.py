"""
Assistant chat and action contracts for dashboard AI features.

Defines request/response models for:
- Assistant chat endpoint (context-aware conversation)
- Assistant action proposals (tool-use and confirmation flows)
"""

import enum
from typing import Any

from pydantic import BaseModel, Field

# ─── Chat Endpoint Contracts (Issue #88) ────────────────────────────────────────


class AssistantContext(BaseModel):
    """Dashboard state context for assistant prompts."""

    current_tab: str = Field(..., description="Active tab: overview, remediation, etc")
    selected_run_id: int | None = None
    selected_items: list[dict] | None = None
    dashboard_state: dict[str, Any] | None = None


class AssistantChatRequest(BaseModel):
    """User prompt + dashboard context for AI assistant."""

    prompt: str = Field(..., min_length=1, max_length=5000)
    context: AssistantContext
    provider: str | None = None  # Override default provider


class AssistantChatResponse(BaseModel):
    """AI assistant response."""

    response: str
    provider: str
    context_used: dict
    timestamp: str  # ISO-8601


# ─── Action Proposal Contracts (Issue #89) ──────────────────────────────────────


class ActionRiskLevel(str, enum.Enum):
    """Risk assessment for proposed actions."""

    LOW = "low"  # Informational, no impact
    MEDIUM = "medium"  # Restarts/reruns, temporary impact
    HIGH = "high"  # Deletes/modifies state
    CRITICAL = "critical"  # Affects hub/entire fleet


class ActionProposal(BaseModel):
    """AI-proposed action for operator approval."""

    action_id: str
    action_type: str  # "restart_runner", "rerun_workflow", etc.
    parameters: dict  # Action-specific params
    description: str  # Human-readable summary
    risk_level: ActionRiskLevel
    rationale: str  # Why the AI thinks this helps
    estimated_duration_seconds: int | None = None


class ActionProposeRequest(BaseModel):
    """Request to propose an action based on user input."""

    user_request: str = Field(..., min_length=1, max_length=5000)
    context: AssistantContext
    provider: str | None = None


class ActionProposeResponse(BaseModel):
    """Proposed action with full details."""

    action_id: str
    action_type: str
    parameters: dict
    description: str
    risk_level: ActionRiskLevel
    rationale: str
    estimated_duration_seconds: int | None = None


class ActionExecuteRequest(BaseModel):
    """Request to execute a proposed action after operator approval."""

    action_id: str
    approved: bool
    operator_notes: str | None = None


class ActionExecuteResponse(BaseModel):
    """Result of executing an action."""

    success: bool
    action_id: str
    result: str  # Outcome message
    execution_time_ms: int
