"""Mint GitHub installation credentials for an active staff run (#2038)."""

from __future__ import annotations

from fastapi import HTTPException
from staff.github_run_env import StaffGithubToken, mint_staff_run_github_token
from staff.roles import load_roles
from staff.runner import get_runner
from staff.store import ACTIVE_STATUSES


def _assert_run_active(run_id: str) -> None:
    store = get_runner().store
    rec = store.get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    if rec.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="run is not active")


def mint_github_credential_for_run(run_id: str) -> StaffGithubToken:
    """Mint a fresh GitHub credential scoped to the run's role and repository."""
    _assert_run_active(run_id)
    store = get_runner().store
    rec = store.get_run(run_id)
    assert rec is not None  # noqa: S101
    roles = load_roles()
    role = roles.get(rec.role)
    if role is None:
        raise HTTPException(status_code=500, detail=f"unknown staff role {rec.role}")
    return mint_staff_run_github_token(role, rec.repo or "")
