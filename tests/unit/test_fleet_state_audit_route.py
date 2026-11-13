"""Unit tests for fleet state audit path resolution."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from routers.fleet_state_audit_route import CANONICAL_STATE_REL, resolve_canonical_state_path


def test_resolve_canonical_state_path_rejects_arbitrary_files() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_canonical_state_path("backend/server.py")
    assert exc.value.status_code == 400


def test_resolve_canonical_state_path_accepts_default_file() -> None:
    path = resolve_canonical_state_path(str(CANONICAL_STATE_REL))
    assert path.name == CANONICAL_STATE_REL.name
