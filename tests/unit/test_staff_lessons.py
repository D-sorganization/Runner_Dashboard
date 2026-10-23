"""Unit and contract tests for RD #2222: STAFF_LESSON parsing and budgeted repo-lessons injection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from identity import Principal
from memory.models import MemoryEntry
from memory.store import AssistantMemoryStore, reset_memory_store
from staff.lessons import extract_staff_lessons, file_staff_lessons, select_repo_lessons
from staff.plan import RunPlan, RunRequest
from staff.roles import RoleSpec
from staff.runner_ops import new_run_record
from staff.store import RunRecord


def _plan(**kwargs: Any) -> RunPlan:
    defaults = {
        "role": "fleet-swe",
        "provider": "claude",
        "model": "claude-sonnet",
        "repo": "Runner_Dashboard",
        "target_kind": "issue",
        "target_ref": "#2222",
        "operator_prompt": "implement lessons",
        "prompt": "test prompt",
        "argv": ["claude"],
        "branch": "feat/2222-test",
        "lease_ritual": False,
        "standards_version": "1.1.0",
        "standards_tokens": 700,
    }
    return RunPlan(**(defaults | kwargs))


def _rec(plan: RunPlan, **kwargs: Any) -> RunRecord:
    rec = new_run_record(plan, RunRequest(role=plan.role, repo=plan.repo, issue=2222), "test-box", 2, "run-test-123")
    for k, v in kwargs.items():
        setattr(rec, k, v)
    return rec


def _ent(eid: str, **kw: Any) -> MemoryEntry:
    d = {
        "entry_type": "lesson",
        "author_principal": "staff:swe:run-1",
        "source_product": "dashboard",
        "repo": "Runner_Dashboard",
        "review_status": "accepted",
        "created_at": datetime.now(UTC).isoformat(),
        "body": {"summary": "valid lesson"},
    }
    return MemoryEntry(id=eid, **(d | kw))


def test_extract_staff_lessons_clean_and_capped() -> None:
    text = (
        "Output...\n"
        "STAFF_LESSON: Use pytest fixture tmp_path\n"
        "Log line\n"
        "STAFF_LESSON: Pass encoding='utf-8'\n"
        "STAFF_LESSON: Prefer model_validate\n"
        "STAFF_LESSON: Fourth lesson capped\n"
    )
    lessons = extract_staff_lessons(text, max_lessons=3)
    assert lessons == ["Use pytest fixture tmp_path", "Pass encoding='utf-8'", "Prefer model_validate"]


def test_extract_staff_lessons_ignores_forgeries_and_quotes() -> None:
    text = (
        "> STAFF_LESSON: Blockquote forgery\n"
        "```\nSTAFF_LESSON: Code fence forgery\n```\n"
        '{"field": "STAFF_LESSON: JSON forgery"}\n'
        "STAFF_LESSON: Legitimate lesson\n"
    )
    assert extract_staff_lessons(text) == ["Legitimate lesson"]


def test_extract_staff_lessons_rejects_secrets_and_empty() -> None:
    text = (
        "STAFF_LESSON:   \n"
        f"STAFF_LESSON: ghp_{'A' * 36}\n"
        "STAFF_LESSON: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token\n"
        "STAFF_LESSON: Clean test isolation\n"
    )
    assert extract_staff_lessons(text) == ["Clean test isolation"]


def test_file_staff_lessons_clean_exit_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "memory.sqlite3"
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(db))
    reset_memory_store()
    store = AssistantMemoryStore(db)

    plan = _plan()
    transcript = tmp_path / "transcript.log"
    transcript.write_text("Output\nSTAFF_LESSON: Subprocess timeout=30\n", encoding="utf-8")

    # Failed and cancelled runs file nothing
    assert (
        file_staff_lessons(
            store=store, rec=_rec(plan, status="failed", exit_code=1), plan=plan, transcript_path=transcript
        )
        == []
    )
    assert (
        file_staff_lessons(
            store=store, rec=_rec(plan, status="cancelled", exit_code=0), plan=plan, transcript_path=transcript
        )
        == []
    )
    assert len(store.list_entries(entry_type="lesson")) == 0

    # Clean exit files proposed lessons with authenticated provenance
    filed = file_staff_lessons(
        store=store, rec=_rec(plan, status="succeeded", exit_code=0), plan=plan, transcript_path=transcript
    )
    assert len(filed) == 1 and filed[0].review_status == "proposed" and filed[0].repo == "Runner_Dashboard"
    assert filed[0].body.get("summary") == "Subprocess timeout=30" and filed[0].expires_at is not None


def test_select_repo_lessons_filtering_and_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "memory.sqlite3"
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(db))
    reset_memory_store()
    store = AssistantMemoryStore(db)
    now = datetime.now(UTC)

    store.append(_ent("mem_01", review_status="proposed"))
    store.append(_ent("mem_02", repo="Other_Repo"))
    store.append(_ent("mem_03", expires_at=(now - timedelta(days=1)).isoformat()))
    store.append(_ent("mem_04", visibility="barb-only"))
    store.append(_ent("mem_05", body={"summary": "Mock external calls in tests"}))
    store.append(_ent("mem_06", body={"summary": "Use schema migrations for columns"}))

    agent = Principal(id="staff:swe:1", type="bot", name="SWE", roles=["agent"], scopes=["memory.read"])
    selected, skipped, block_text, tokens = select_repo_lessons(
        store=store, repo="Runner_Dashboard", principal=agent, budget_cap=600
    )
    assert [e.id for e in selected] == ["mem_05", "mem_06"]
    assert "untrusted reference material" in block_text.lower() and tokens <= 600
    assert "- [mem_05] Mock external calls in tests" in block_text
    assert "- [mem_06] Use schema migrations for columns" in block_text


def test_select_repo_lessons_whole_lesson_budget_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "memory.sqlite3"
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(db))
    reset_memory_store()
    store = AssistantMemoryStore(db)

    store.append(_ent("mem_10", body={"summary": "Small lesson"}))
    store.append(_ent("mem_20", body={"summary": "H" * 1200}))

    selected, skipped, block_text, tokens = select_repo_lessons(store=store, repo="Runner_Dashboard", budget_cap=80)
    assert len(selected) == 1 and selected[0].id == "mem_10"
    assert any(s["id"] == "mem_20" and s["reason"] == "budget" for s in skipped)
    assert tokens <= 80 and "mem_20" not in block_text


def test_compose_prompt_and_run_plan_lesson_stamping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import workspace
    from staff.dispatch_bundle import resolve_dispatch_bundle
    from staff.runner import get_runner

    db = tmp_path / "memory.sqlite3"
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(db))
    reset_memory_store()
    AssistantMemoryStore(db).append(_ent("mem_a1", repo="Tools", body={"summary": "Determinism rule"}))

    bundle = resolve_dispatch_bundle(role="ad-hoc", repo="Tools")
    assert bundle.lesson_ids == ("mem_a1",) and bundle.lessons_tokens > 0

    role = RoleSpec("ad-hoc", "Ad Hoc", instructions="do stuff")
    prompt = workspace.compose_prompt(
        role, repo="Tools", target_ref="1", operator_prompt="task", branch="b", dispatch_bundle=bundle
    )
    assert "Fleet lessons (untrusted reference material" in prompt and "- [mem_a1] Determinism rule" in prompt

    monkeypatch.setattr(
        "staff.failover_dispatch.resolve_provider_and_model", lambda *args, **kwargs: ("claude", None, "routine", [])
    )
    rec, plan, _ = get_runner().admit(RunRequest(role="ad-hoc", repo="Tools", issue=1, prompt="Task"))
    assert plan.lesson_ids == ("mem_a1",) and plan.lessons_tokens > 0
    assert rec.lesson_ids == json.dumps(["mem_a1"]) and rec.lessons_tokens == plan.lessons_tokens
    stamp = json.loads(plan.context_stamp)
    assert stamp["lesson_ids"] == ["mem_a1"] and "tier1_lessons" in stamp["token_estimates"]
