"""Pydantic shapes for the priorities API (issue #1227). DbC: every parser output passes through these."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ActivePriority(BaseModel):
    """One ``### Active Priorities`` entry of a board consensus."""

    rank: int = Field(ge=1)
    item: str = Field(min_length=1)
    project: str = ""
    scope: str = ""
    assigned_to: str = ""
    tracking: str = ""
    acceptance: str = ""


class DeferredItem(BaseModel):
    """One ``### Deferred Backlog`` table row."""

    item: str = Field(min_length=1)
    project: str = ""
    reason: str = ""
    reassess: str = ""


class BordaRow(BaseModel):
    """One ``## Borda Count Results`` table row; ``score`` is None when not a number."""

    rank: int = Field(ge=1)
    item: str = Field(min_length=1)
    project: str = ""
    score: int | None = None
    votes: str = ""


class MeetingMetadata(BaseModel):
    date: str = ""
    quorum: str = ""
    instruction_writer: str = ""
    consensus_reached: str = ""


class Consensus(BaseModel):
    """A parsed ``consensus.md``. Unfilled template placeholders are dropped, never reported as items."""

    metadata: MeetingMetadata = Field(default_factory=MeetingMetadata)
    active: list[ActivePriority] = Field(default_factory=list)
    deferred: list[DeferredItem] = Field(default_factory=list)
    borda: list[BordaRow] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)


class Portfolio(BaseModel):
    """One ``portfolios.<name>`` entry of RM ``config/fleet_manifest.yaml``."""

    name: str = Field(min_length=1)
    description: str = ""
    wip_limit: int | None = Field(default=None, ge=0)
    review_cadence: str = ""
    unattended_agents: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
