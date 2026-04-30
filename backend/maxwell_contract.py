"""Maxwell-Daemon contract models — dashboard consumer view.

This module defines the *dashboard's* view of the Maxwell-Daemon API.
Responses from Maxwell are deserialized into these models so that:

1. Only allow-listed fields are forwarded to the frontend.
2. Sensitive fields (e.g., ``secret_token``, ``api_key``) are never leaked.
3. Schema drift (Maxwell adds/renames a field) is caught at the boundary.

Contract version: v1 (2026-04-30, issue #366).
Docs: docs/contracts/maxwell.md

Usage::

    from maxwell_contract import MaxwellVersionResponse
    raw: dict = await _mx_get("/api/version")
    return MaxwellVersionResponse.model_validate(raw).model_dump()
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared sentinel — use this for any field that must not be forwarded.
# ---------------------------------------------------------------------------

_SENSITIVE_FIELDS = frozenset(
    {
        "secret_token",
        "api_key",
        "api_secret",
        "token",
        "password",
        "private_key",
        "connection_string",
        "db_url",
        "webhook_secret",
        "signing_secret",
        "client_secret",
    }
)


# ---------------------------------------------------------------------------
# /api/version
# ---------------------------------------------------------------------------


class MaxwellVersionResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/version endpoint."""

    version: str = Field(default="unknown", description="Semantic version string")
    build: str | None = Field(default=None, description="Build hash or CI label")
    environment: str | None = Field(default=None)
    started_at: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# /api/status  (pipeline state / daemon status)
# ---------------------------------------------------------------------------


class MaxwellStatusResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/status endpoint."""

    state: str = Field(default="unknown")
    active_tasks: int = Field(default=0, ge=0)
    queued_tasks: int = Field(default=0, ge=0)
    completed_tasks: int | None = Field(default=None, ge=0)
    failed_tasks: int | None = Field(default=None, ge=0)
    uptime_seconds: float | None = Field(default=None)
    last_activity: str | None = Field(default=None)
    paused: bool = Field(default=False)


# ---------------------------------------------------------------------------
# /api/tasks  (list)
# ---------------------------------------------------------------------------


class MaxwellTaskItem(BaseModel):
    """A single task entry in the tasks list."""

    id: str = Field(description="Task UUID")
    status: str = Field(default="unknown")
    created_at: str | None = Field(default=None)
    updated_at: str | None = Field(default=None)
    type: str | None = Field(default=None)
    priority: int | None = Field(default=None)
    tags: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None)
    # No credential fields are allow-listed here.


class MaxwellTaskListResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/tasks list endpoint."""

    tasks: list[MaxwellTaskItem] = Field(default_factory=list)
    cursor: str | None = Field(default=None, description="Opaque pagination cursor")
    total: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# /api/tasks/{task_id}  (detail)
# ---------------------------------------------------------------------------


class MaxwellTaskDetailResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/tasks/{task_id} endpoint."""

    id: str
    status: str = Field(default="unknown")
    created_at: str | None = Field(default=None)
    updated_at: str | None = Field(default=None)
    started_at: str | None = Field(default=None)
    completed_at: str | None = Field(default=None)
    type: str | None = Field(default=None)
    priority: int | None = Field(default=None)
    tags: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None)
    result_summary: str | None = Field(default=None)
    # Note: full result payload is intentionally omitted — use a dedicated
    # result endpoint if needed, and filter there too.


# ---------------------------------------------------------------------------
# /api/v1/backends
# ---------------------------------------------------------------------------


class MaxwellBackendItem(BaseModel):
    """A single backend provider entry. Sensitive config is strip-listed."""

    name: str = Field(description="Backend display name, e.g. 'Anthropic'")
    type: str = Field(default="unknown")
    enabled: bool = Field(default=False)
    model: str | None = Field(default=None)
    status: str | None = Field(default=None)
    # connection_string, api_key, etc. are deliberately NOT listed here.


class MaxwellBackendsResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/v1/backends endpoint."""

    backends: list[MaxwellBackendItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /api/v1/workers
# ---------------------------------------------------------------------------


class MaxwellWorkerItem(BaseModel):
    """A single worker entry."""

    id: str
    status: str = Field(default="idle")
    current_task_id: str | None = Field(default=None)
    tasks_completed: int | None = Field(default=None, ge=0)
    tasks_failed: int | None = Field(default=None, ge=0)
    started_at: str | None = Field(default=None)
    last_activity: str | None = Field(default=None)


class MaxwellWorkersResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/v1/workers endpoint."""

    workers: list[MaxwellWorkerItem] = Field(default_factory=list)
    total: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# /api/v1/cost
# ---------------------------------------------------------------------------


class MaxwellCostResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/v1/cost endpoint."""

    total_usd: float | None = Field(default=None, ge=0)
    window: str | None = Field(default=None, description="e.g. 'rolling_30d'")
    by_model: dict[str, float] | None = Field(default=None)
    by_backend: dict[str, float] | None = Field(default=None)
    currency: str = Field(default="USD")


# ---------------------------------------------------------------------------
# /api/control/{action}  (pipeline control response)
# ---------------------------------------------------------------------------


class MaxwellControlResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/v1/control/{action} response."""

    action: str
    status: str = Field(default="ok")
    message: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# /api/v1/tasks  (dispatch response)
# ---------------------------------------------------------------------------


class MaxwellDispatchResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's task dispatch (POST /api/v1/tasks)."""

    task_id: str = Field(alias="id", default="unknown")
    status: str = Field(default="queued")
    idempotency_key: str | None = Field(default=None)
    created_at: str | None = Field(default=None)
    message: str | None = Field(default=None)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def strip_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove known-sensitive keys from a dict before forwarding.

    This is a defence-in-depth utility called before model validation when
    the raw upstream response is passed directly (e.g., from _mx_get).
    """
    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        if k in _SENSITIVE_FIELDS:
            continue
        if isinstance(v, dict):
            cleaned[k] = strip_sensitive(v)
        elif isinstance(v, list):
            cleaned[k] = [strip_sensitive(item) if isinstance(item, dict) else item for item in v]
        else:
            cleaned[k] = v
    return cleaned
