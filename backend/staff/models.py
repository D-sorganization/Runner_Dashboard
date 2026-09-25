"""Staff Hub response and request models (issue #1289, #1296).

Precondition: All models strictly match backend store / runner / schedule / audit contracts.
Postcondition: Exported schemas define the canonical OpenAPI contract for frontend types.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StaffConsolidateWhen(BaseModel):
    """Thresholds for consolidating PRs (#1213)."""

    open_prs: int | None = None
    utilisation_pct: float | None = None

    model_config = ConfigDict(extra="allow")


class StaffRoleStrategy(BaseModel):
    """Strategy configuration for a role (#1213)."""

    consolidate_when: StaffConsolidateWhen | None = None

    model_config = ConfigDict(extra="allow")


class StaffRoleBudget(BaseModel):
    """Budget limits for a role."""

    usd_per_run: float | None = None
    usd_per_day: float | None = None

    model_config = ConfigDict(extra="allow")


class StaffRoleSpec(BaseModel):
    """Detailed role specification exposed on the roster."""

    name: str
    title: str
    summary: str = ""
    playbook: str = ""
    prompt_template: str | None = None
    instructions: str = ""
    providers: list[str]
    model: str | None = None
    schedule: str | None = None
    window: dict[str, str] | str | None = None
    repos: list[str]
    scope: dict[str, Any] = Field(default_factory=dict)
    budget: StaffRoleBudget
    idle_minutes: float = 20.0
    permissions: dict[str, Any] = Field(default_factory=dict)
    reports_to: str | None = None
    holds: list[str]
    surface: str | None = "dashboard"
    retired: bool = False
    retired_reason: str = ""
    strategy: StaffRoleStrategy = Field(default_factory=StaffRoleStrategy)
    persona: Any = ""
    chat: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    defers_to: list[str] = Field(default_factory=list)
    group: str | None = None
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    error: str | None = None
    dispatchable: bool = True
    source_path: str = ""
    active_runs: int = 0

    model_config = ConfigDict(extra="allow")


class StaffRosterResponse(BaseModel):
    """Response model for GET /api/staff/roster and GET /api/staff/roles."""

    machine: str
    roles: list[StaffRoleSpec]
    providers: dict[str, bool]
    active_runs: int = 0

    model_config = ConfigDict(extra="allow")


class StaffRunRecord(BaseModel):
    """One staff execution run record."""

    id: str
    role: str = ""
    provider: str = ""
    model: str | None = None
    machine: str = ""
    repo: str = ""
    target_kind: str = "prompt"
    target_ref: str = ""
    prompt: str = ""
    status: str = "queued"
    requested_by: str = ""
    on_behalf_of: str = ""
    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    workdir: str = ""
    branch: str = ""
    transcript_path: str = ""
    lease_id: str = ""
    error: str = ""
    last_line: str = ""
    cost_method: str = ""
    strategy_mode: str = ""
    outcome: str = ""
    failure_class: str = ""
    pid: int | None = None
    retryable: bool = False
    remediation: str = ""
    retry_of: str = ""
    attempt: int = 1
    max_attempts: int = 2
    next_attempt_at: str | None = None
    fallback_provider: str = ""

    model_config = ConfigDict(extra="allow")


class StaffRunsResponse(BaseModel):
    """Response model for GET /api/staff/runs."""

    runs: list[StaffRunRecord]
    count: int = 0

    model_config = ConfigDict(extra="allow")


class StaffRunEvent(BaseModel):
    """One event in a staff run's lifecycle."""

    seq: int
    ts: str
    kind: str
    text: str

    model_config = ConfigDict(extra="allow")


class StaffRunDetailResponse(BaseModel):
    """Response model for GET /api/staff/runs/{id}."""

    run: StaffRunRecord
    events: list[StaffRunEvent]
    attempts: list[StaffRunRecord] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class StaffCancelResponse(BaseModel):
    """Response model for POST /api/staff/runs/{id}/cancel."""

    cancelled: bool
    run: StaffRunRecord | None = None

    model_config = ConfigDict(extra="allow")


class StaffConsolidationDecision(BaseModel):
    """Consolidation decision injected into prompt (#1213)."""

    mode: str
    reason: str
    threshold: StaffConsolidateWhen = Field(default_factory=StaffConsolidateWhen)

    model_config = ConfigDict(extra="allow")


class StaffRunPlan(BaseModel):
    """Dry-run execution plan for a staff run (#1213)."""

    role: str = ""
    provider: str = ""
    model: str | None = None
    repo: str = ""
    target_kind: str = "prompt"
    target_ref: str = ""
    prompt: str = ""
    argv: list[str] = Field(default_factory=list)
    branch: str = ""
    lease_ritual: bool = True
    consolidation: StaffConsolidationDecision | None = None

    model_config = ConfigDict(extra="allow")


class StaffDispatchResponse(BaseModel):
    """Response model for POST /api/staff/{role}/run."""

    dry_run: bool
    machine: str
    plan: StaffRunPlan | None = None
    run: StaffRunRecord | None = None
    forwarded_to: str | None = None

    model_config = ConfigDict(extra="allow")


class StaffRoleLiveness(BaseModel):
    """Liveness record of one scheduled role (#1209)."""

    role: str
    schedule: str = ""
    status: str = "ok"
    last_success: str | None = None
    last_attempt: str | None = None
    last_fired: str | None = None
    next_fire: str | None = None
    expected_interval_seconds: float | None = None
    age_seconds: float | None = None
    machine: str | None = None

    model_config = ConfigDict(extra="allow")


class StaffBoardResponse(BaseModel):
    """Response model for GET /api/staff/board (issue #1289)."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    machine: str = Field(description="Local machine hostname")
    hub: str | None = Field(default=None, description="Hub machine hostname when aggregated")
    running: list[StaffRunRecord]
    queued: list[StaffRunRecord]
    recent: list[StaffRunRecord]
    spend_today_usd: dict[str, float] = Field(
        description="Per-provider spend in USD plus a 'total' key (issue #1289)",
    )
    providers: dict[str, Any]
    liveness: list[StaffRoleLiveness] = Field(default_factory=list)
    liveness_alerts: list[StaffRoleLiveness] = Field(default_factory=list)
    machines: dict[str, dict[str, Any]] | None = Field(default=None)
    online: list[str] = Field(default_factory=list)
    offline: list[str] = Field(default_factory=list)
    rm_source: dict[str, Any] | None = Field(default=None)

    model_config = ConfigDict(extra="allow")


class StaffHold(BaseModel):
    """One policy hold."""

    id: str
    text: str
    set_on: str
    lifted_when: str
    applies_to: list[str]
    active: bool

    model_config = ConfigDict(extra="allow")


class StaffSummaryResponse(BaseModel):
    """Response model for GET /api/staff/summary (issue #1289)."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    hub: str = Field(description="Hub machine hostname")
    machines_online: list[str] = Field(default_factory=list)
    machines_offline: list[str] = Field(default_factory=list)
    in_flight: list[dict[str, Any]] = Field(default_factory=list)
    recent_24h: dict[str, int] = Field(default_factory=dict)
    attention: list[dict[str, Any]] = Field(default_factory=list)
    spend_today_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Per-provider spend in USD plus a 'total' key (issue #1289)",
    )
    providers: dict[str, Any] = Field(default_factory=dict)
    holds: list[dict[str, Any]] = Field(default_factory=list)
    liveness_alerts: list[StaffRoleLiveness] = Field(default_factory=list)
    roles: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class StaffAuditRecordResponse(BaseModel):
    """One staff audit entry."""

    id: int | str
    ts: str
    principal: str
    on_behalf_of: str | None = None
    surface: str
    action: str
    target: str
    request_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    outcome: str = "success"
    detail: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class StaffAuditListResponse(BaseModel):
    """Response model for GET /api/staff/audit."""

    entries: list[StaffAuditRecordResponse] = Field(default_factory=list)
    count: int = 0
    total: int = 0

    model_config = ConfigDict(extra="allow")


class StaffHoldsResponse(BaseModel):
    """Response model for GET /api/staff/holds and PUT /api/staff/holds."""

    holds: list[StaffHold]
    path: str = ""

    model_config = ConfigDict(extra="allow")


class StaffScheduleToggleResponse(BaseModel):
    """Response model for POST /api/staff/schedule/toggle."""

    enabled: bool
    running: bool

    model_config = ConfigDict(extra="allow")


class StaffScheduleResponse(BaseModel):
    """Response model for GET /api/staff/schedule."""

    machine: str
    generated_at: str
    enabled: bool
    running: bool
    tick_seconds: float = 60.0
    roles: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class StaffUsageResponse(BaseModel):
    """Response model for GET /api/staff/usage."""

    machine: str
    since: str
    group: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    budget_per_day_usd: float | None = None
    spend_today_usd: float = 0.0

    model_config = ConfigDict(extra="allow")


class StaffPricingResponse(BaseModel):
    """Response model for GET /api/staff/usage/pricing."""

    rows: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class StaffUsageExportResponse(BaseModel):
    """Response model for POST /api/staff/usage/export."""

    ok: bool
    message: str = ""
    totals: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")
