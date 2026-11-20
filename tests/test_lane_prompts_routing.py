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
        lane_id="agy-node-b-1",
        role="backlog-completion",
        repos=["Tools"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="gemini-3.8-flash",
        max_children=3,
    )
    store.register_assignment(
        lane_id="claude-hub-a-1",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="claude-haiku-5-5",
        max_children=3,
    )
    store.register_assignment(
        lane_id="codex-node-c-1",
        role="backlog-completion",
        repos=["AffineDrift"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="",
        max_children=0,
    )

    # Claude on hub-a must NOT claim agy-node-b-1 even though agy-node-b-1 is first alphabetically.
    c_claude = store.checkout(
        session_id="claude-hub-a-20261117-1",
        principal="loopback-dev",
        idempotency_key="k1",
        host="hub-a",
    )
    assert c_claude["lane_id"] == "claude-hub-a-1"

    # A second Claude on hub-a must get LaneConflictError rather than stealing agy-node-b-1 or codex-node-c-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="claude-hub-a-20261117-2",
            principal="loopback-dev",
            idempotency_key="k2",
            host="hub-a",
        )

    # Antigravity on node-b claims agy-node-b-1.
    c_agy = store.checkout(
        session_id="agy-node-b-20261117-1",
        principal="loopback-dev",
        idempotency_key="k3",
        provider="agy",
        host="node-b",
    )
    assert c_agy["lane_id"] == "agy-node-b-1"


def test_explicit_lane_id_and_unknown_metadata_enforce_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit lane_id must reject incompatible provider/host, and unknown metadata must not steal scoped lanes."""
    from coordination.lane_prompts import LaneConflictError, LanePromptStore

    monkeypatch.setenv("RUNNER_DASHBOARD_HOST_ALIASES", '{"hub-a-full":"hub-a"}')
    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="agy-node-b-1",
        role="backlog-completion",
        repos=["Tools"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="gemini-3.8-flash",
        max_children=3,
    )
    store.register_assignment(
        lane_id="codex-node-c-1",
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
            session_id="claude-hub-a-20261117-9",
            principal="loopback-dev",
            idempotency_key="k-explicit-bad",
            lane_id="agy-node-b-1",
            provider="claude",
            host="hub-a-full",
        )

    # Host inferred from session_id ('codex-hub-a') must not claim codex-node-c-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="codex-hub-a-20261117-1",
            principal="loopback-dev",
            idempotency_key="k-host-inferred",
        )

    # Unrecognized session without provider must not claim provider-scoped agy-node-b-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="worker-review-1",
            principal="loopback-dev",
            idempotency_key="k-unknown-prov",
        )

    # Known provider without host must NOT claim host-scoped agy-node-b-1.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="agy-session-1",
            principal="loopback-dev",
            idempotency_key="k-missing-host",
            provider="agy",
        )

    # Prefix collision check: node-backup must NOT match node-b.
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="agy-node-backup-20261117-1",
            principal="loopback-dev",
            idempotency_key="k-prefix-collision",
            provider="agy",
            host="node-backup",
        )

    # Reused idempotency key with incompatible provider/host must be rejected, not replayed.
    ok = store.checkout(
        session_id="agy-node-b-20261117-2",
        principal="loopback-dev",
        idempotency_key="k-replay-check",
        provider="agy",
        host="node-b",
    )
    assert ok["lane_id"] == "agy-node-b-1"
    with pytest.raises(LaneConflictError, match="incompatible"):
        store.checkout(
            session_id="agy-node-b-20261117-2",
            principal="loopback-dev",
            idempotency_key="k-replay-check",
            provider="claude",
            host="node-b",
        )


def test_cursor_lane_routing_and_composer_model(tmp_path: Path) -> None:
    """Cursor lanes and composer-* models route to cursor provider with host aliases."""
    from coordination.lane_prompts import (
        LanePromptStore,
        _lane_host,
        _lane_provider,
    )

    # Provider and model inference
    assert _lane_provider("cursor-desk-1", "") == "cursor"
    assert _lane_provider("some-custom-lane", "composer-2.5") == "cursor"
    assert _lane_provider("some-custom-lane", "grok-code") == "cursor"

    # Host inference
    assert _lane_host("cursor-desk-1") == "desk"
    assert _lane_host("cursor-ct-1") == "ct"
    assert _lane_host("cursor-node-1") == "node"

    # Store registration and catalog
    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="cursor-desk-1",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc123",
        package_hash="pkg123",
        subagent_model="composer-2.5",
        max_children=2,
    )
    store.register_assignment(
        lane_id="cursor-ct-1",
        role="acceptance-review",
        repos=["AffineDrift"],
        release_sha="abc123",
        package_hash="pkg123",
        subagent_model="composer-2.5",
        max_children=2,
    )
    catalog = store.list_catalog()
    cat_ids = {c["lane_id"] for c in catalog}
    assert "cursor-desk-1" in cat_ids
    assert "cursor-ct-1" in cat_ids

    # Checkout cursor lane by session id
    res = store.checkout(
        session_id="cursor-desk-20261118-1",
        principal="agent-cursor",
        idempotency_key="idem-cursor-1",
        host="desk",
    )
    assert res["lane_id"] == "cursor-desk-1"


def test_checkout_accepts_staff_registry_provider_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit ``cursor-agent`` / ``deepseek-api`` must match seeded lane provider ids."""
    from coordination.lane_prompts import LaneConflictError, LanePromptStore, normalize_lane_provider

    monkeypatch.setenv("RUNNER_DASHBOARD_HOST_ALIASES", '{"hub-a":"hub-a"}')
    assert normalize_lane_provider("cursor-agent") == "cursor"
    assert normalize_lane_provider("deepseek-api") == "deepseek"
    assert normalize_lane_provider("grok-cli") == "cursor"

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.register_assignment(
        lane_id="deepseek-hub-a-1",
        role="mad-scientist",
        repos=["Runner_Dashboard"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="deepseek-v4-pro",
        max_children=1,
    )
    store.register_assignment(
        lane_id="cursor-hub-a-1",
        role="backlog-completion",
        repos=["Runner_Dashboard"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="composer-2.5",
        max_children=2,
    )

    deepseek = store.checkout(
        session_id="staff-deepseek-hub-a-20261119-1",
        principal="agent-deepseek",
        idempotency_key="k-deepseek-api",
        provider="deepseek-api",
        host="hub-a",
    )
    assert deepseek["lane_id"] == "deepseek-hub-a-1"

    cursor = store.checkout(
        session_id="staff-cursor-hub-a-20261119-1",
        principal="agent-cursor",
        idempotency_key="k-cursor-agent",
        provider="cursor-agent",
        host="hub-a",
    )
    assert cursor["lane_id"] == "cursor-hub-a-1"

    with pytest.raises(LaneConflictError, match="incompatible"):
        store.checkout(
            session_id="staff-cursor-hub-a-20261119-2",
            principal="agent-cursor",
            idempotency_key="k-wrong-prov",
            lane_id="deepseek-hub-a-1",
            provider="cursor-agent",
            host="hub-a",
        )
