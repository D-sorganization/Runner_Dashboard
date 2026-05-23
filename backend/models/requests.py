"""Typed pydantic request models for every POST /api/* route (issue #716).

These models enforce DbC preconditions at the API boundary so route handlers
receive validated, typed data — never raw dicts.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ── Reusable field types ──────────────────────────────────────────────────────

GitHubRepoFullName = Annotated[
    str,
    Field(
        min_length=3,
        max_length=200,
        pattern=r"^[\w.\-]+/[\w.\-]+$",
        description="GitHub full repository name (owner/repo)",
    ),
]

WorkflowRunId = Annotated[int, Field(gt=0, description="GitHub Actions run ID")]


# ── Request models ────────────────────────────────────────────────────────────


class HelpChatRequest(BaseModel):
    """Request body for POST /api/help/chat."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Question to ask the assistant",
    )

    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class LauncherGenerateRequest(BaseModel):
    """Request body for POST /api/launchers/generate."""

    repo_full: GitHubRepoFullName
    workflow_file: str = Field(default="", max_length=200)
    ref: str = Field(default="main", max_length=200)

    model_config = {"extra": "forbid"}


class FleetNodeControlRequest(BaseModel):
    """Request body for fleet node control endpoints."""

    action: Literal["start", "stop", "restart"] = Field(..., description="Action to perform")
    runner_name: str = Field(..., min_length=1, max_length=200)

    model_config = {"extra": "forbid"}
