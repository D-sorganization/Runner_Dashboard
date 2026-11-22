"""Default hub lane catalog seeding for CLI agent lanes (#4565 follow-up)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_lane_prompt_store_seeds_cursor_qwen_deepseek_hub_lanes(tmp_path: Path) -> None:
    from coordination.lane_prompts import LanePromptStore, _lane_provider

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.seed_defaults()
    catalog = {row["lane_id"]: row for row in store.list_catalog()}
    for lid in (
        "cursor-ct-1",
        "cursor-oglaptop-1",
        "cursor-ct-2",
        "cursor-oglaptop-2",
        "qwen-ct-1",
        "qwen-oglaptop-1",
        "deepseek-ct-1",
        "deepseek-oglaptop-1",
    ):
        assert lid in catalog, lid
        assert catalog[lid]["enabled"] is True
    assert catalog["cursor-ct-1"]["subagent_model"] == "composer-2.5"
    assert catalog["cursor-ct-2"]["subagent_model"] == "grok-4.7-high"
    assert "cursor-desk-1" not in catalog
    assert _lane_provider("qwen-ct-1", "") == "qwen"
    assert _lane_provider("deepseek-oglaptop-1", "") == "deepseek"
    assert _lane_provider("cursor-ct-2", "grok-4.7-high") == "cursor"


def test_seed_adds_ct_host_codex_worker_without_changing_review_seats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coordination.lane_prompts import LaneConflictError, LanePromptStore

    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RUNNER_DASHBOARD_HOST_ALIASES", '{"example-ct-host": "ct"}')
    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    for lane in ("codex-ct-test", "claude-ct-1"):
        store.register_assignment(
            lane_id=lane,
            role="acceptance-review",
            repos=["UpstreamDrift"],
            release_sha="review",
            package_hash="review",
            subagent_model="review",
            max_children=1,
        )
    store.seed_defaults()
    store.seed_defaults()
    catalog = {row["lane_id"]: row for row in store.list_catalog()}
    assert catalog["codex-ct-test"]["role"] == "acceptance-review"
    assert catalog["claude-ct-1"]["role"] == "acceptance-review"
    worker = catalog["codex-ct-1"]
    assert worker["repos"] == ["UpstreamDrift", "AffineDrift"]
    assert worker["subagent_model"] == "gpt-6-luna"
    assert worker["max_children"] == 3
    assert worker["enabled"] is True
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="wrong-host",
            principal="agent-codex",
            idempotency_key="wrong-host",
            provider="codex",
            host="example-other-host",
            role="backlog-completion",
        )
    receipt = store.checkout(
        session_id="worker",
        principal="agent-codex",
        idempotency_key="1",
        provider="codex",
        host="example-ct-host",
        role="backlog-completion",
    )
    assert receipt["lane_id"] == "codex-ct-1"
    with pytest.raises(LaneConflictError):
        store.checkout(
            session_id="other",
            principal="agent-codex",
            idempotency_key="2",
            provider="codex",
            host="example-ct-host",
            role="backlog-completion",
        )
    review = store.checkout(
        session_id="reviewer",
        principal="agent-codex",
        idempotency_key="3",
        provider="codex",
        host="example-ct-host",
        role="acceptance-review",
    )
    assert review["lane_id"] == "codex-ct-test"
