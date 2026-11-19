"""Default hub lane catalog seeding for Cursor CLI lanes (#4565 follow-up)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_lane_prompt_store_seeds_cursor_hub_lanes(tmp_path: Path) -> None:
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.seed_defaults()
    catalog = {row["lane_id"]: row for row in store.list_catalog()}
    assert "cursor-ct-1" in catalog
    assert "cursor-oglaptop-1" in catalog
    assert catalog["cursor-ct-1"]["enabled"] is True
    assert catalog["cursor-oglaptop-1"]["enabled"] is True
    assert catalog["cursor-ct-1"]["subagent_model"] == "composer-2.5"
    assert "cursor-desk-1" in catalog
    assert catalog["cursor-desk-1"]["enabled"] is False