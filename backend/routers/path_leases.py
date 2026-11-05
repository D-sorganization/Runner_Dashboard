"""HTTP router for TTL path/issue leases (``/api/v1/path-leases``, #3931 S1).

A local, fast lease primitive distinct from the GitHub-issue lease wrapped by
``/api/coordination/claims``: this one never touches GitHub and exists for a
pre-launch/pre-publish re-check hook (S2) so a dispatcher can atomically
reserve a path glob or ``issue:<n>`` before starting work.

Auth: reads use ``require_fleet_peer``; writes need ``coordination.write``
(same perimeter as ``routers/fleet_friction.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from identity import require_fleet_peer, require_scope
from path_lease_store import LeaseConflictError, PathLeaseRecord, PathLeaseStore, default_path_lease_db_path
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/path-leases", tags=["path-leases"])
_require_writer = require_scope("coordination.write")
FleetPeer = Annotated[str, Depends(require_fleet_peer)]
_T = TypeVar("_T")


def _store() -> PathLeaseStore:
    return PathLeaseStore(default_path_lease_db_path())


def _or_409(call: Callable[[], _T]) -> _T:
    """Run a store write, turning ``LeaseConflictError`` into a 409."""
    try:
        return call()
    except LeaseConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class LeaseOut(BaseModel):
    lease_id: str
    repo: str
    scope: str
    owner: str
    acquired_at: float
    expires_at: float

    @classmethod
    def from_record(cls, rec: PathLeaseRecord) -> LeaseOut:
        return cls(**asdict(rec))


class CheckOut(BaseModel):
    held: bool
    lease: LeaseOut | None


class AcquireBody(BaseModel):
    repo: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=128)
    ttl_seconds: float = Field(gt=0, le=86400)


class RenewBody(BaseModel):
    lease_id: str = Field(min_length=1, max_length=64)
    owner: str = Field(min_length=1, max_length=128)
    ttl_seconds: float = Field(gt=0, le=86400)


class ReleaseBody(BaseModel):
    lease_id: str = Field(min_length=1, max_length=64)
    owner: str = Field(min_length=1, max_length=128)


@router.get("/check")
def check_lease(
    _peer: FleetPeer,
    repo: str = Query(min_length=1, max_length=200),
    scope: str = Query(min_length=1, max_length=500),
) -> CheckOut:
    rec = _store().check(repo, scope)
    return CheckOut(held=rec is not None, lease=LeaseOut.from_record(rec) if rec else None)


@router.post("/acquire")
def acquire_lease(body: AcquireBody, _who: str = Depends(_require_writer)) -> LeaseOut:
    rec = _or_409(lambda: _store().acquire(body.repo, body.scope, body.owner, body.ttl_seconds))
    return LeaseOut.from_record(rec)


@router.post("/renew")
def renew_lease(body: RenewBody, _who: str = Depends(_require_writer)) -> LeaseOut:
    rec = _or_409(lambda: _store().renew(body.lease_id, body.owner, body.ttl_seconds))
    return LeaseOut.from_record(rec)


@router.post("/release", status_code=204)
def release_lease(body: ReleaseBody, _who: str = Depends(_require_writer)) -> Response:
    _or_409(lambda: _store().release(body.lease_id, body.owner))
    return Response(status_code=204)
