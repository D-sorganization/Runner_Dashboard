"""Maxwell-Daemon contract models — dashboard consumer view.

This module defines the *dashboard's* view of the Maxwell-Daemon API.
Responses from Maxwell are deserialized into these models so that:

1. Only allow-listed fields are forwarded to the frontend.
2. Sensitive fields (e.g., ``secret_token``, ``api_key``) are never leaked.
3. Schema drift (Maxwell adds/renames a field) is caught at the boundary.

Contract version: 2.0.0 (matches Maxwell-Daemon ``CONTRACT_VERSION``; issues
#955/#956/#958). Earlier this module modelled an imaginary "v1" shape with zero
overlapping keys against the daemon, so every field silently defaulted and the
Maxwell tab perpetually showed "unknown / 0 tasks". The models below validate the
daemon's REAL response shapes (see ``maxwell_daemon/api/contract.py``) and make
the discriminating field of each response **required**, so future drift fails
loudly (a ``ValidationError`` the proxy surfaces as 502) instead of defaulting.

Docs: docs/contracts/maxwell.md

Usage::

    from maxwell_contract import MaxwellVersionResponse
    raw: dict = await _mx_get("/api/version")
    return MaxwellVersionResponse.model_validate(raw).model_dump()
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

# The contract major version this dashboard build was written against. Surfaced
# at the /api/version boundary so a major-version mismatch with the daemon's
# advertised ``contract`` can be detected and shown as a degraded-mode banner
# instead of rendering silently-defaulted data (#956).
EXPECTED_CONTRACT_VERSION = "2.0.0"
EXPECTED_CONTRACT_MAJOR = EXPECTED_CONTRACT_VERSION.split(".", 1)[0]

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
    """Consumer view of Maxwell-Daemon's ``GET /api/version`` endpoint (#956).

    MD returns ``{daemon, contract}`` (``maxwell_daemon/api/contract.py:VersionResponse``).
    ``daemon`` is the daemon's semantic version; ``contract`` is the surface
    contract version used for consumer negotiation. Both are **required** so a
    reachable-but-incompatible daemon fails loudly here instead of reporting
    ``version="unknown"`` forever.

    The derived ``version`` field preserves the dashboard-facing name the frontend
    already reads, and ``contract_compatible`` is computed against the contract
    version this build targets so the Maxwell tab can show an explicit
    incompatibility banner rather than silently rendering defaulted data.
    """

    daemon: str = Field(description="Daemon semantic version (MD 'daemon' field)")
    contract: str = Field(description="MD surface contract version, e.g. '2.0.0'")
    # Dashboard-facing alias retained for the existing frontend (mirrors `daemon`).
    version: str = Field(default="unknown", description="Semantic version string")
    contract_compatible: bool = Field(default=True)

    @model_validator(mode="after")
    def _derive(self) -> MaxwellVersionResponse:
        # Mirror the daemon version onto the legacy `version` key the UI reads.
        self.version = self.daemon
        # Major-version compatibility: a mismatch is a breaking contract change.
        daemon_major = self.contract.split(".", 1)[0]
        self.contract_compatible = daemon_major == EXPECTED_CONTRACT_MAJOR
        return self


# ---------------------------------------------------------------------------
# /api/status  (pipeline state / daemon status)
# ---------------------------------------------------------------------------


class MaxwellStatusResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's ``GET /api/status`` endpoint (#955).

    MD returns ``{pipeline_state, active_task_id, gate, sandbox}``
    (``maxwell_daemon/api/contract.py:StatusResponse``). ``pipeline_state`` is the
    discriminating field and is **required** so a shape mismatch fails loudly
    instead of reporting ``state="unknown"`` against a healthy daemon.

    ``pipeline_state`` is mapped onto the dashboard-facing ``state`` key; ``paused``
    is derived from it; ``active_tasks`` is derived from ``active_task_id``
    presence. Richer task counts come from ``GET /api/v2/status`` via
    :class:`MaxwellStatusV2Response` and are merged in by the proxy route, which
    sets ``active_tasks``/``queued_tasks``/``completed_tasks``/``failed_tasks``
    from the authoritative ``counts`` map when available.
    """

    pipeline_state: str = Field(description="MD pipeline state: idle/running/paused/error")
    active_task_id: str | None = Field(default=None)
    gate: str | None = Field(default=None, description="MD admission gate: open/closed")
    sandbox: str | None = Field(default=None, description="enabled/disabled")
    # Dashboard-facing derived fields (mirrors / counts).
    state: str = Field(default="unknown")
    active_tasks: int = Field(default=0, ge=0)
    queued_tasks: int = Field(default=0, ge=0)
    completed_tasks: int | None = Field(default=None, ge=0)
    failed_tasks: int | None = Field(default=None, ge=0)
    uptime_seconds: float | None = Field(default=None)
    last_activity: str | None = Field(default=None)
    paused: bool = Field(default=False)

    @model_validator(mode="after")
    def _derive(self) -> MaxwellStatusResponse:
        self.state = self.pipeline_state
        self.paused = self.pipeline_state == "paused"
        # Baseline from /api/status; refined by merge_v2_counts() when the richer
        # /api/v2/status payload is available.
        self.active_tasks = 1 if self.active_task_id else 0
        return self

    def merge_v2_counts(self, v2: MaxwellStatusV2Response) -> None:
        """Overlay authoritative task counts from ``GET /api/v2/status`` (#955).

        ``StatusV2Response.counts`` is a ``{state: n}`` map. We read the standard
        states defensively (missing keys default to 0) so a daemon that omits a
        bucket does not crash the merge.
        """
        counts = v2.counts
        self.active_tasks = int(counts.get("running", self.active_tasks))
        self.queued_tasks = int(counts.get("queued", counts.get("pending", 0)))
        self.completed_tasks = int(counts.get("completed", counts.get("done", 0)))
        self.failed_tasks = int(counts.get("failed", counts.get("error", 0)))


class MaxwellStatusV2Response(BaseModel):
    """Consumer view of Maxwell-Daemon's ``GET /api/v2/status`` endpoint (#955).

    Only the ``counts`` map is contract-relevant for the dashboard's task tallies;
    ``counts`` is **required** so a shape change is caught loudly. The richer
    ``running``/``retrying`` task arrays are intentionally not modelled field-by-
    field here (they are large and unstable); they are ignored by this consumer.
    """

    counts: dict[str, int] = Field(description="Per-state task counts, e.g. {'running': 2}")

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# /api/tasks  (list)
# ---------------------------------------------------------------------------


class MaxwellTaskItem(BaseModel):
    """A single task entry in the tasks list (issue #961).

    Mirrors MD's real ``TaskSummary`` (``maxwell_daemon/api/contract.py``):
    ``{id, status, created_at}``. ``id`` and ``status`` are **required** so a
    defaulted task row (the old failure mode where every field silently fell back
    to ``unknown``) is impossible. The phantom fields RD previously modelled
    (``updated_at``/``type``/``priority``/``tags``/``error``) had no producer in
    MD and were dropped; reinstate them here only once MD's ``TaskSummary`` adds
    them (tracked in the paired Maxwell_Daemon issue).
    """

    id: str = Field(description="Task UUID")
    status: str = Field(description="Task status, e.g. queued/running/completed")
    created_at: str | None = Field(default=None)
    # No credential fields are allow-listed here.


class MaxwellTaskListResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/tasks list endpoint (issue #961).

    MD's list response keys pagination as ``next_cursor`` (``tasks.py``), not
    ``cursor``; the old mis-keyed ``cursor`` field meant the dashboard could never
    advance pages. ``next_cursor`` mirrors MD's field directly. MD currently
    always returns ``next_cursor=None`` (pagination is stubbed upstream); the
    proxy/UI therefore must not offer a "next page" affordance until MD emits a
    real cursor — tracked in the paired Maxwell_Daemon issue.
    """

    tasks: list[MaxwellTaskItem] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, description="MD pagination cursor (None until MD implements it)")
    total: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# /api/tasks/{task_id}  (detail)
# ---------------------------------------------------------------------------


class MaxwellTaskDetailResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's /api/tasks/{task_id} endpoint (issue #961).

    Mirrors MD's real ``TaskDetail`` (``maxwell_daemon/api/contract.py``):
    ``{id, status, created_at, transcript, artifacts}``. ``id``/``status`` are
    **required**. ``transcript`` and ``artifacts`` are always emitted by MD (as
    ``[]`` today — the fields are present but unpopulated), so they are modelled
    as lists with empty-list defaults rather than the phantom
    ``updated_at``/``type``/``priority``/``tags``/``error``/``result_summary``
    fields RD previously invented, none of which MD produces. The phantom fields
    are dropped; re-add when MD's ``TaskDetail`` grows a real producer.
    """

    id: str
    status: str = Field(description="Task status, e.g. queued/running/completed")
    created_at: str | None = Field(default=None)
    transcript: list[Any] = Field(default_factory=list, description="MD task transcript ([] until populated)")
    artifacts: list[Any] = Field(default_factory=list, description="MD task artifacts ([] until populated)")


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

    @field_validator("backends", mode="before")
    @classmethod
    def _normalize_daemon_backends(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [{"name": item, "enabled": True} if isinstance(item, str) else item for item in value]


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
    """Consumer view of Maxwell-Daemon's ``GET /api/v1/workers`` endpoint (#958).

    MD returns ``{worker_count, queue_depth}`` (``maxwell_daemon/api/routes/fleet.py``)
    — it does NOT (yet) emit per-worker items. ``worker_count`` is **required** so a
    shape change fails loudly instead of silently rendering an empty list.

    ``total`` mirrors ``worker_count`` for the existing frontend; ``queue_depth`` is
    surfaced for the workers panel. The ``workers`` list stays empty until MD
    enriches the endpoint with per-worker detail (tracked on the MD side).
    """

    worker_count: int = Field(ge=0, description="Number of active workers (MD field)")
    queue_depth: int | None = Field(default=None, ge=0, description="Pending queue depth (MD field)")
    # Dashboard-facing fields.
    workers: list[MaxwellWorkerItem] = Field(default_factory=list)
    total: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _derive(self) -> MaxwellWorkersResponse:
        self.total = self.worker_count
        return self


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
    """Consumer view of Maxwell-Daemon's ``POST /api/control/{action}`` response.

    MD's ``ControlResponse`` (maxwell_daemon/api/contract.py) is
    ``{action, applied_at, previous_state}``. We mirror those fields and keep
    ``action`` required so a mismatched payload fails loudly (DbC) instead of
    silently defaulting (issue #952). The legacy ``status``/``message`` fields are
    retained as optional for backward compatibility with the dashboard tab and
    older MD builds, but are no longer the contract's source of truth.
    """

    action: str
    applied_at: str | None = Field(default=None)
    previous_state: str | None = Field(default=None)
    # Legacy/optional — the Maxwell tab and older MD builds may still read these.
    status: str = Field(default="ok")
    message: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# /api/dispatch  (dispatch response, #953)
# ---------------------------------------------------------------------------


class MaxwellDispatchResponse(BaseModel):
    """Consumer view of Maxwell-Daemon's task dispatch (POST /api/dispatch).

    Issue #953: the dashboard now proxies dispatch to MD's confirmation-gated,
    idempotent ``POST /api/dispatch`` (``DispatchResponse`` =
    ``{task_id, status, queued_at}``) instead of ``POST /api/v1/tasks`` (which
    silently dropped ``confirmation_token``/``idempotency_key`` under Pydantic's
    default ``extra="ignore"``). ``id``/``created_at`` aliases are retained so a
    daemon still answering the legacy shape validates without error during the
    additive rollover (reversible — DbC).
    """

    task_id: str = Field(validation_alias=AliasChoices("task_id", "id"))
    status: str = Field(default="queued")
    idempotency_key: str | None = Field(default=None)
    queued_at: str | None = Field(default=None)
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
