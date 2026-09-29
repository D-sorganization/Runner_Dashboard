"""Staff knowledge and scorecard response models (Issue #1479, #1517).

Split from ``staff.models`` to keep that module under the repo's 500-line soft
cap (same pattern as ``routers.staff_knowledge``, itself split out of
``routers.staff_v1`` for the same reason).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StaffKnowledgePassage(BaseModel):
    """One retrieved knowledge chunk with citation (Issue #1479)."""

    repo: str
    source: str
    anchor: str
    title: str
    text: str
    commit: str
    content_hash: str
    status: str
    authority: str
    score: float
    citation: str

    model_config = ConfigDict(extra="allow")


class StaffKnowledgeInfoResponse(BaseModel):
    """Response model for GET /api/v1/staff/knowledge/{pack_id} (Issue #1479)."""

    pack_id: str
    title: str
    built_at: str
    commits: dict[str, str] = Field(default_factory=dict)
    stale: bool
    files: int
    passages: int
    passage_count: int

    model_config = ConfigDict(extra="allow")


class StaffKnowledgeSearchResponse(BaseModel):
    """Response model for GET /api/v1/staff/knowledge/{pack_id}/search (Issue #1479)."""

    pack_id: str
    query: str
    count: int
    passages: list[StaffKnowledgePassage] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class StaffOutcomeRow(BaseModel):
    """One scorecard row per role, provider or repo (#1517). A ``None`` rate means no data."""

    key: str
    runs: int
    succeeded: int
    verified: int
    failed_verification: int
    cost_usd: float
    prs: int
    prs_unknown: int
    merged: int
    closed_unmerged: int
    open: int
    ci_first_pass: int
    fix_within_48h: int
    merge_rate: float | None
    verified_rate: float | None
    ci_first_pass_rate: float | None
    fix_within_48h_rate: float | None
    cost_per_merged_pr: float | None


class StaffOutcomesResponse(BaseModel):
    """``GET /api/v1/staff/outcomes`` (#1517)."""

    machine: str
    group_by: str
    since: str
    rows: list[StaffOutcomeRow]
    totals: StaffOutcomeRow
    prs_truncated: bool = False
