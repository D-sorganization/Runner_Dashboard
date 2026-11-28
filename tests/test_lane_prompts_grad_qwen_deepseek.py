"""Regression for owner-authorized graduation of Qwen/DeepSeek CLI lanes.

Ensures qwen-ct-1, qwen-oglaptop-1, deepseek-ct-1, and deepseek-oglaptop-1 are
seeded as production backlog-completion lanes (not mad-scientist draft-only).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_GRADUATED_LANES = (
    "qwen-ct-1",
    "qwen-oglaptop-1",
    "deepseek-ct-1",
    "deepseek-oglaptop-1",
)
_PRODUCTION_ROLE = "backlog-completion"


def test_graduated_lanes_have_production_role(tmp_path: Path) -> None:
    """Qwen/DeepSeek lanes must carry backlog-completion, not mad-scientist."""
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.seed_defaults()
    catalog = {row["lane_id"]: row for row in store.list_catalog()}

    for lane_id in _GRADUATED_LANES:
        assert lane_id in catalog, f"lane {lane_id!r} missing from catalog"
        role = catalog[lane_id]["role"]
        assert role == _PRODUCTION_ROLE, (
            f"lane {lane_id!r} has role={role!r}; expected {_PRODUCTION_ROLE!r}"
        )


def test_graduated_lanes_task_focus_has_no_draft_only(tmp_path: Path) -> None:
    """task_focus must not contain 'draft-only' for graduated lanes."""
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.seed_defaults()
    catalog = {row["lane_id"]: row for row in store.list_catalog()}

    for lane_id in _GRADUATED_LANES:
        focus = catalog[lane_id].get("task_focus", "")
        assert "draft-only" not in focus.lower(), (
            f"lane {lane_id!r} task_focus still mentions 'draft-only': {focus!r}"
        )


def test_graduated_lanes_task_focus_instructs_non_draft_pr(tmp_path: Path) -> None:
    """task_focus must instruct lanes to open non-draft PRs targeting main."""
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    store.seed_defaults()
    catalog = {row["lane_id"]: row for row in store.list_catalog()}

    for lane_id in _GRADUATED_LANES:
        focus = catalog[lane_id].get("task_focus", "").lower()
        assert "non-draft" in focus or "nondraft" in focus, (
            f"lane {lane_id!r} task_focus lacks non-draft PR instruction: {focus!r}"
        )
        assert "main" in focus, (
            f"lane {lane_id!r} task_focus does not mention 'main' target: {focus!r}"
        )


def test_provider_registry_deepseek_note_has_no_draft_only_qualification() -> None:
    """The DeepSeek ProviderEntry notes must not reference 'draft-only qualification'."""
    from agent_remediation.provider_registry import PROVIDER_REGISTRY

    deepseek_entry = next(
        (e for e in PROVIDER_REGISTRY if getattr(e, "conductor_id", None) == "deepseek-api"),
        None,
    )
    assert deepseek_entry is not None, "DeepSeek provider entry not found in PROVIDER_REGISTRY"
    notes = getattr(deepseek_entry, "notes", "") or ""
    if isinstance(notes, (list, tuple)):
        notes = " ".join(notes)
    assert "draft-only qualification" not in notes.lower(), (
        f"DeepSeek provider notes still reference 'draft-only qualification': {notes!r}"
    )
