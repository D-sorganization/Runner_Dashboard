"""Shared FastAPI query validators for repo short names."""

from __future__ import annotations

from coordination.models import bare_repo
from fastapi import HTTPException


def bare_repo_query(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return bare_repo(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
