"""Tests for built-in prompt templates (premortem)."""

from __future__ import annotations

from pathlib import Path

from code_requests.builtin_templates import load_builtin_templates, merge_with_builtins


def test_premortem_template_ships_with_report_sections() -> None:
    by_name = {t["name"]: t for t in load_builtin_templates()}
    premortem = by_name["premortem"]
    assert premortem["builtin"] is True
    assert premortem["prompt"] == premortem["content"]
    for section in (
        "Stage 1. The Autopsy",
        "Stage 2. The Verdict",
        "Stage 3. The Rebuild",
        "Stage 4. The Adversary",
        "Stage 5. The Tripwires",
        "## Draft Issues",
    ):
        assert section in premortem["content"]


def test_loader_skips_empty_files_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("B body", encoding="utf-8")
    (tmp_path / "a.md").write_text("A body", encoding="utf-8")
    (tmp_path / "empty.md").write_text("   ", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    assert [t["name"] for t in load_builtin_templates(tmp_path)] == ["a", "b"]


def test_saved_template_overrides_builtin_of_same_name() -> None:
    builtins = [{"name": "premortem", "content": "builtin", "prompt": "builtin", "builtin": True}]
    saved = [{"name": "premortem", "content": "mine"}, {"name": "other", "content": "x"}]
    merged = merge_with_builtins(saved, builtins)
    assert [t["name"] for t in merged] == ["premortem", "other"]
    assert merged[0]["prompt"] == "mine"
    assert "builtin" not in merged[0]


def test_merge_adds_prompt_alias_and_ignores_non_dicts() -> None:
    merged = merge_with_builtins([{"name": "s", "content": "body"}, "junk"], [])  # type: ignore[list-item]
    assert merged == [{"name": "s", "content": "body", "prompt": "body"}]
