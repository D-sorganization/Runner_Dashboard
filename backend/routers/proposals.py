"""Board Proposals API endpoints (issue #1284, CR-7).

Routes (all under /api/proposals):
  POST ""                  Create a suggestion for the Board (requires proposals.write).
  GET ""                   List proposals with optional state=open|decided and repo filter.
  GET /{number}            Get proposal detail with Board-Secretary comments.

Auth:
  POST requires principal holding 'proposals.write' (operator or bot principal).
  GET routes use require_fleet_peer (fleet peers and operators).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from identity import Principal, require_fleet_peer, require_scope
from proposals import service
from proposals.models import (
    CreateProposalRequest,
    ProposalDetail,
    ProposalItem,
    ProposalsListResponse,
)

log = logging.getLogger("dashboard.routers.proposals")
router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@router.post(
    "",
    response_model=ProposalItem,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a proposal to the Board",
)
async def create_proposal(
    body: CreateProposalRequest,
    caller: Principal = Depends(require_scope("proposals.write")),  # noqa: B008
) -> Any:
    """Submit a suggestion to the Board with duplicate check and rate limiting."""
    log.info("Proposal submission by %s (%s): %s", caller.id, caller.type, body.title)
    return await service.create_proposal(body, caller)


@router.get(
    "",
    response_model=ProposalsListResponse,
    summary="List board proposals",
)
async def list_proposals(
    state: str | None = Query(default=None, description="Filter by state: open, decided"),
    repo: str | None = Query(default=None, description="Filter by target repo"),
    _peer: str = Depends(require_fleet_peer),  # noqa: B008
) -> Any:
    """List proposals with decision outcomes and meeting links."""
    items = await service.list_proposals(state=state, repo=repo)
    return ProposalsListResponse(proposals=items, total=len(items))


@router.get(
    "/{number}",
    response_model=ProposalDetail,
    summary="Get proposal detail and Secretary comments",
)
async def get_proposal(
    number: int,
    _peer: str = Depends(require_fleet_peer),  # noqa: B008
) -> Any:
    """Get single proposal details and comments from the Board-Secretary."""
    return await service.get_proposal(number)
