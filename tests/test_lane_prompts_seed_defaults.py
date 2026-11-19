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