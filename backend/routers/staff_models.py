"""Pydantic response and contract models for Staff Hub APIs (SC-A10, Issue #1296).

Provides strongly-typed schemas for OpenAPI documentation, client code generation,
and runtime response validation across /api/staff/* and /api/v1/staff/* routes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StaffRoleBudget(BaseModel):
    """Budget constraints configured for a role."""

    usd_per_run: float | None = Field(default=None, description="Max spend per run in USD")
    usd_per_day: float | None = Field(default=None, description="Max daily spend in USD")
    max_minutes: int | None = Field(default=None, description="Execution timeout in minutes")
    idle_minutes: int | None = Field(default=None, description="Idle timeout in minutes")

    model_config = ConfigDict(extra="allow")


class StaffConsolidateWhen(BaseModel):
    """PR consolidation thresholds."""

    open_prs: int | None = Field(default=None, description="Open PR count threshold")
    utilisation_pct: float | None = Field(default=None, description="Fleet utilisation percentage threshold")

    model_config = ConfigDict(extra="allow")


class StaffRoleStrategy(BaseModel):
    """Role consolidation strategy."""

    consolidate_when: StaffConsolidateWhen | None = Field(default=None, description="Consolidation thresholds")

    model_config = ConfigDict(extra="allow")


class StaffRoleSpec(BaseModel):
    """Full role specification returned by the roster."""

    name: str = Field(description="Role identifier")
    title: str = Field(default="", description="Human-readable title")
    summary: str = Field(default="", description="Role summary description")
    playbook: str = Field(default="", description="Path or name of playbook")
    prompt_template: str | None = Field(default=None, description="Prompt template")
    instructions: str | None = Field(default=None, description="System instructions")
    providers: list[str] = Field(description="Supported provider IDs")
    model: str | None = Field(default=None, description="Default model override")
    schedule: str | None = Field(default=None, description="Cron or periodic schedule")
    window: Any | None = Field(default=None, description="Execution window constraint")
    repos: list[str] = Field(description="Allowed or target repositories")
    scope: dict[str, Any] = Field(default_factory=dict, description="Execution scope")
    budget: StaffRoleBudget = Field(description="Budget parameters")
    idle_minutes: int | None = Field(default=None, description="Idle timeout")
    permissions: dict[str, Any] = Field(default_factory=dict, description="Permission definitions")
    reports_to: str | None = Field(default=None, description="Supervising role identifier")
    holds: list[str] = Field(description="Active hold tags")
    surface: str | None = Field(default=None, description="Target execution surface")
    retired: bool = Field(default=False, description="Whether role is retired")
    retired_reason: str | None = Field(default=None, description="Reason for retirement")
    strategy: StaffRoleStrategy = Field(default_factory=StaffRoleStrategy, description="Consolidation strategy")
    persona: str | None = Field(default=None, description="Personality/tone guidelines")
    chat: dict[str, Any] = Field(default_factory=dict, description="Chat parameters")
    group: str | None = Field(default=None, description="Group affiliation")
    valid: bool = Field(default=True, description="Whether role YAML is valid")
    errors: list[str] = Field(default_factory=list, description="Validation errors if invalid")
    error: str | None = Field(default=None, description="Primary error message")
    dispatchable: bool = Field(default=True, description="Whether role can be dispatched")
    source_path: str = Field(default="", description="Path to role definition file")
    active_runs: int = Field(default=0, description="Count of currently active runs")

    model_config = ConfigDict(extra="allow")


class StaffRosterResponse(BaseModel):
    """Response for GET /roster."""

    machine: str = Field(description="Local machine hostname")
    roles: list[StaffRoleSpec] = Field(description="Available roles")
    providers: dict[str, bool] = Field(description="Provider availability map")
    active_runs: int = Field(description="Total active runs across roles")

    model_config = ConfigDict(extra="allow")


class StaffRunRecord(BaseModel):
    """Flat row for one staff run."""

    id: str = Field(description="Unique run ID")
    role: str = Field(description="Role name")
    provider: str = Field(description="Provider name")
    model: str | None = Field(default=None, description="Model used")
    machine: str = Field(description="Machine hostname")
    repo: str = Field(default="", description="Target repository")
    target_kind: str = Field(default="prompt", description="Target kind: issue | pr | prompt")
    target_ref: str = Field(default="", description="Target reference")
    prompt: str = Field(default="", description="Execution prompt")
    status: str = Field(default="queued", description="Run status")
    requested_by: str = Field(default="", description="Principal or user who requested run")
    created_at: str = Field(description="ISO-8601 creation timestamp")
    started_at: str | None = Field(default=None, description="ISO-8601 start timestamp")
    ended_at: str | None = Field(default=None, description="ISO-8601 end timestamp")
    exit_code: int | None = Field(default=None, description="Process exit code")
    cost_usd: float = Field(default=0.0, description="Recorded cost in USD")
    input_tokens: int = Field(default=0, description="Input tokens consumed")
    output_tokens: int = Field(default=0, description="Output tokens consumed")
    workdir: str = Field(default="", description="Working directory")
    branch: str = Field(default="", description="Git branch")
    transcript_path: str = Field(default="", description="Path to transcript log")
    lease_id: str = Field(default="", description="Fleet lease identifier")
    error: str = Field(default="", description="Error message if failed")
    last_line: str = Field(default="", description="Last stdout line captured")
    cost_method: str = Field(default="", description="Method used to calculate cost")
    strategy_mode: str = Field(default="", description="Consolidation strategy mode")
    outcome: str = Field(default="", description="Normalised outcome summary")
    failure_class: str = Field(default="", description="Failure classification")
    pid: int | None = Field(default=None, description="Worker process PID")
    retryable: bool = Field(default=False, description="Whether failure is retryable")
    remediation: str = Field(default="", description="Remediation instructions")

    model_config = ConfigDict(extra="allow")


class StaffRunEvent(BaseModel):
    """Event emitted during run execution."""

    seq: int = Field(description="Sequence number")
    ts: str = Field(description="ISO-8601 timestamp")
    kind: str = Field(description="Event kind")
    text: str = Field(description="Event content")

    model_config = ConfigDict(extra="allow")


class StaffRoleLiveness(BaseModel):
    """Liveness status of a scheduled role."""

    role: str = Field(description="Role identifier")
    schedule: str = Field(description="Schedule expression")
    status: str = Field(description="Liveness status: ok | late | dead | never")
    last_success: str | None = Field(default=None, description="Last successful run timestamp")
    last_attempt: str | None = Field(default=None, description="Last attempt timestamp")
    last_fired: str | None = Field(default=None, description="Last fired timestamp")
    next_fire: str | None = Field(default=None, description="Next scheduled run timestamp")
    expected_interval_seconds: float | None = Field(default=None, description="Expected interval")
    age_seconds: float | None = Field(default=None, description="Seconds since last run")
    machine: str | None = Field(default=None, description="Machine reporting this status")

    model_config = ConfigDict(extra="allow")


class StaffBoardResponse(BaseModel):
    """Response model for /api/staff/board."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    machine: str | None = Field(default=None, description="Local machine hostname")
    hub: str | None = Field(default=None, description="Hub machine hostname when aggregated")
    running: list[StaffRunRecord] = Field(default_factory=list, description="Currently running jobs")
    queued: list[StaffRunRecord] = Field(default_factory=list, description="Queued jobs")
    recent: list[StaffRunRecord] = Field(default_factory=list, description="Recently completed jobs")
    spend_today_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Per-provider spend in USD plus a 'total' key",
    )
    providers: dict[str, Any] = Field(default_factory=dict, description="Provider availability")
    liveness: list[StaffRoleLiveness] = Field(default_factory=list, description="Local role liveness")
    liveness_alerts: list[StaffRoleLiveness] = Field(default_factory=list, description="Fleet-wide alerts")
    machines: dict[str, dict[str, Any]] | None = Field(default=None, description="Per-node board maps")
    online: list[str] = Field(default_factory=list, description="Online node hostnames")
    offline: list[str] = Field(default_factory=list, description="Offline node hostnames")
    rm_source: dict[str, Any] | None = Field(default=None, description="Repository Management sync status")

    model_config = ConfigDict(extra="allow")


class StaffSummaryResponse(BaseModel):
    """Response model for /api/staff/summary."""

    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    hub: str = Field(description="Hub machine hostname")
    machines_online: list[str] = Field(default_factory=list)
    machines_offline: list[str] = Field(default_factory=list)
    in_flight: list[StaffRunRecord] = Field(default_factory=list, description="Runs currently in flight")
    recent_24h: dict[str, int] = Field(default_factory=dict, description="Run count summary in last 24h")
    attention: list[dict[str, Any]] = Field(default_factory=list)
    spend_today_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Per-provider spend in USD plus a 'total' key",
    )
    providers: dict[str, Any] = Field(default_factory=dict, description="Provider availability")
    holds: list[dict[str, Any]] = Field(default_factory=list)
    liveness_alerts: list[StaffRoleLiveness] = Field(default_factory=list)
    roles: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class StaffRunsResponse(BaseModel):
    """Response model for /api/staff/runs."""

    runs: list[StaffRunRecord] = Field(default_factory=list, description="Matching run rows")
    count: int = Field(description="Number of runs returned")

    model_config = ConfigDict(extra="allow")


class StaffRunDetailResponse(BaseModel):
    """Response model for /api/staff/runs/{run_id}."""

    run: StaffRunRecord = Field(description="Run details")
    events: list[StaffRunEvent] = Field(default_factory=list, description="Execution events")

    model_config = ConfigDict(extra="allow")


class StaffCancelResponse(BaseModel):
    """Response model for /api/staff/runs/{run_id}/cancel."""

    cancelled: bool = Field(description="Whether the cancellation request succeeded")
    run: StaffRunRecord | None = Field(default=None, description="Updated run record")

    model_config = ConfigDict(extra="allow")


class StaffConsolidationDecision(BaseModel):
    """Consolidation decision injected into runner prompt."""

    mode: str = Field(default="", description="Consolidation mode: consolidate | serial")
    reason: str = Field(default="", description="Rationale for the decision")
    threshold: StaffConsolidateWhen = Field(default_factory=StaffConsolidateWhen, description="Evaluated thresholds")

    model_config = ConfigDict(extra="allow")


class StaffRunPlan(BaseModel):
    """Execution plan generated by StaffRunner."""

    role: str = Field(default="", description="Role identifier")
    provider: str = Field(default="", description="Provider name")
    model: str | None = Field(default=None, description="Model override")
    repo: str = Field(default="", description="Repository name")
    target_kind: str = Field(default="prompt", description="Target kind")
    target_ref: str = Field(default="", description="Target reference")
    prompt: str = Field(default="", description="Prepared prompt")
    argv: list[str] = Field(default_factory=list, description="CLI arguments")
    branch: str = Field(default="", description="Branch name")
    lease_ritual: bool = Field(default=False, description="Whether lease rituals are enforced")
    consolidation: StaffConsolidationDecision | None = Field(default=None, description="Consolidation plan")

    model_config = ConfigDict(extra="allow")


class StaffDispatchResponse(BaseModel):
    """Response model for POST /api/staff/{role}/run."""

    dry_run: bool = Field(description="Whether dispatch was a dry-run preview")
    machine: str = Field(description="Executing machine hostname")
    plan: StaffRunPlan | None = Field(default=None, description="Plan preview if dry_run=True")
    run: StaffRunRecord | None = Field(default=None, description="Submitted run if dry_run=False")
    forwarded_to: str | None = Field(default=None, description="Peer hostname if forwarded")

    model_config = ConfigDict(extra="allow")


class StaffAuditEntry(BaseModel):
    """Single row from the durable append-only staff audit log."""

    id: int | str = Field(description="Audit row ID")
    ts: str = Field(description="ISO-8601 UTC timestamp")
    principal: str = Field(description="Caller identity")
    on_behalf_of: str = Field(default="", description="Delegated principal if any")
    surface: str = Field(default="", description="Entry surface (api, scheduler, etc.)")
    action: str = Field(description="Action executed")
    target: str = Field(description="Target entity")
    request_id: str = Field(default="", description="HTTP request ID")
    thread_id: str = Field(default="", description="Conversation thread ID")
    run_id: str = Field(default="", description="Associated run ID")
    outcome: str = Field(default="success", description="Outcome: success | failure | etc.")
    detail: dict[str, Any] = Field(default_factory=dict, description="Structured detail payload")

    model_config = ConfigDict(extra="allow")


class StaffAuditResponse(BaseModel):
    """Response model for GET /api/staff/audit."""

    entries: list[StaffAuditEntry] = Field(default_factory=list, description="Matching audit rows")
    count: int = Field(description="Number of rows in this page")
    total: int = Field(description="Total count matching filter")

    model_config = ConfigDict(extra="allow")


class StaffHold(BaseModel):
    """Hold rule restricting role dispatch."""

    id: str = Field(description="Hold identifier")
    text: str = Field(description="Reason for the hold")
    set_on: str = Field(default="", description="Timestamp when set")
    lifted_when: str = Field(default="", description="Condition or date to lift")
    applies_to: list[str] = Field(default_factory=list, description="Roles or repos affected")
    active: bool = Field(default=True, description="Whether hold is active")

    model_config = ConfigDict(extra="allow")


class StaffHoldsResponse(BaseModel):
    """Response model for GET and PUT /api/staff/holds."""

    holds: list[StaffHold] = Field(default_factory=list, description="Current holds")
    path: str | None = Field(default=None, description="Storage path on disk")

    model_config = ConfigDict(extra="allow")


class StaffToggleScheduleResponse(BaseModel):
    """Response model for POST /api/staff/schedule/toggle."""

    enabled: bool = Field(description="Configured state")
    running: bool = Field(description="Runtime execution state")

    model_config = ConfigDict(extra="allow")


class StaffScheduleResponse(BaseModel):
    """Response model for GET /api/staff/schedule."""

    machine: str = Field(description="Node hostname")
    generated_at: str = Field(description="ISO-8601 UTC timestamp")
    enabled: bool = Field(description="Configured state")
    running: bool = Field(description="Runtime state")
    roles: list[dict[str, Any]] = Field(default_factory=list, description="Scheduled roles")

    model_config = ConfigDict(extra="allow")


class StaffUsageTotals(BaseModel):
    """Aggregated usage metrics."""

    runs: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    wall_seconds: float = Field(default=0.0)

    model_config = ConfigDict(extra="allow")


class StaffUsageBudget(BaseModel):
    """Daily budget tracking."""

    usd_per_day: float = Field(default=0.0)
    spent_today_usd: float = Field(default=0.0)
    percent_used: float | None = Field(default=None)

    model_config = ConfigDict(extra="allow")


class StaffUsageResponse(BaseModel):
    """Response model for GET /api/staff/usage."""

    group: str = Field(description="Grouping dimension")
    since: str | None = Field(default=None, description="Start timestamp filter")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="Aggregated rows")
    totals: StaffUsageTotals = Field(default_factory=StaffUsageTotals, description="Totals across rows")
    budget: StaffUsageBudget = Field(default_factory=StaffUsageBudget, description="Budget status")
    machine: str = Field(default="", description="Node hostname")

    model_config = ConfigDict(extra="allow")


class StaffPricingResponse(BaseModel):
    """Response model for GET /api/staff/usage/pricing."""

    rows: list[dict[str, Any]] = Field(default_factory=list, description="Pricing table rows")

    model_config = ConfigDict(extra="allow")


class StaffExportUsageResponse(BaseModel):
    """Response model for POST /api/staff/usage/export."""

    ok: bool = Field(description="Export success status")
    date: str = Field(default="", description="Export date")
    rm_root: str = Field(default="", description="Repository Management path")
    exported: list[dict[str, Any]] = Field(default_factory=list, description="Export details per provider")

    model_config = ConfigDict(extra="allow")
