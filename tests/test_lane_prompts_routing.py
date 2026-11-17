"""TDD regression tests for provider/host filtering in LanePromptStore.checkout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_checkout_matches_provider_and_host_from_session_id(tmp_path: Path) -> None:
    from coordination.lane_prompts import LaneConflictError, LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="agy-oglaptop-1",
        role="backlog-completion",
        repos=["Tools"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="gemini-3.8-flash",
        max_children=3,
    )
    store.register_assignment(
        lane_id="claude-desk-1",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="claude-haiku-5-5",
        max_children=3,
    )
    store.register_assignment(
        lane_id="codex-ct-1",
        role="backlog-completion",
        repos=["AffineDrift"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="",
        max_children=0,
    )

    # Claude on DeskComputer must NOT claim agy-oglaptop-1 even though agy-oglaptop-1 is first alphabetically.
    c_claude = store.checkout(
        session_id="claude-DeskComputer-20261117-1",
        principal="loopback-dev",
        idempotency_key="k1",
        host="deskcomputer",
    )
    assert c_claude["lane_id"] == "claude-desk-1"

    # A second Claude on DeskComputer must get LaneConflictError rather than stealing agy-oglaptop-1 or codex-ct-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="claude-DeskComputer-20261117-2",
            principal="loopback-dev",
            idempotency_key="k2",
            host="deskcomputer",
        )

    # Antigravity on OGLaptop claims agy-oglaptop-1.
    c_agy = store.checkout(
        session_id="agy-OGLaptop-20261117-1",
        principal="loopback-dev",
        idempotency_key="k3",
        provider="agy",
        host="oglaptop",
    )
    assert c_agy["lane_id"] == "agy-oglaptop-1"


def test_explicit_lane_id_and_unknown_metadata_enforce_compatibility(tmp_path: Path) -> None:
    """Explicit lane_id must reject incompatible provider/host, and unknown metadata must not steal scoped lanes."""
    from coordination.lane_prompts import LaneConflictError, LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="agy-oglaptop-1",
        role="backlog-completion",
        repos=["Tools"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="gemini-3.8-flash",
        max_children=3,
    )
    store.register_assignment(
        lane_id="codex-ct-1",
        role="backlog-completion",
        repos=["AffineDrift"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="",
        max_children=0,
    )

    # Explicit incompatible lane_id must be rejected.
    with pytest.raises(LaneConflictError, match="incompatible"):
        store.checkout(
            session_id="claude-DeskComputer-20261117-9",
            principal="loopback-dev",
            idempotency_key="k-explicit-bad",
            lane_id="agy-oglaptop-1",
            provider="claude",
            host="deskcomputer",
        )

    # Host inferred from session_id ('codex-DeskComputer') must not claim codex-ct-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="codex-DeskComputer-20261117-1",
            principal="loopback-dev",
            idempotency_key="k-host-inferred",
        )

    # Unrecognized session without provider must not claim provider-scoped agy-oglaptop-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="worker-review-1",
            principal="loopback-dev",
            idempotency_key="k-unknown-prov",
        )
