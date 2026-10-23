"""Unit and contract tests for memory v2 lesson entries (RD #2221)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from memory.contract import validate_status_transition
from memory.models import MemoryAppendRequest, MemoryEntry, MemoryReviewRequest
from memory.service import append_entry, get_prompt_lessons, list_entries, review_entry
from memory.store import AssistantMemoryStore, reset_memory_store
from server import app


def _p(role: str = "agent", run_id: str = "run-1") -> Principal:
    sc = ["memory.read", "memory.write"] + (["memory.review"] if role == "lib" else [])
    k = ("lib", "librarian") if role == "lib" else (f"staff:swe:{run_id}", f"staff-run:{run_id}")
    return Principal(id=k[0], type="bot", name="B", roles=[k[1]], scopes=sc)


def _req(**kw: Any) -> MemoryAppendRequest:
    d = dict(entry_type="lesson", source_product="d", repo="RD", body={"s": "x"})
    return MemoryAppendRequest(**(d | kw))


def _ent(eid: str, **kw: Any) -> MemoryEntry:
    d = dict(entry_type="lesson", author_principal="a", source_product="d", repo="RD", review_status="accepted")
    return MemoryEntry(
        id=eid, created_at=datetime.now(UTC).isoformat(), source_run="run-1", body={"s": "x"}, **(d | kw)
    )


def test_memory_lesson_store_and_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "memory.sqlite3"
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(db))
    reset_memory_store()
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE memory_entries (id, entry_type, author_principal, source_product, "
            "tags_json, body_json, created_at, expires_at, supersedes_id, superseded_by)"
        )
        conn.execute("INSERT INTO memory_entries VALUES ('o', 'task', 'u', 'c', '[]', '{}', '2026', NULL, NULL, NULL)")
    store = AssistantMemoryStore(db)
    assert (old := store.get("o")) and old.repo is None
    store.append(_ent("mem_l1", review_status="proposed"))
    assert len(store.list_entries(entry_type="lesson", repo="RD")) == 1

    agent, lib, now = _p("agent", "run-abc"), _p("lib"), datetime.now(UTC)
    e = append_entry(agent, _req(body={"summary": "TDD fixes."}))
    assert e.review_status == "proposed" and e.source_run == "run-abc" and e.visibility == "fleet" and e.expires_at

    for bad in (_req(review_status="accepted"), _req(source_run="spoof")):
        pytest.raises(HTTPException, append_entry, agent, bad)
    for req in (
        _req(expires_at=(now - timedelta(days=1)).isoformat()),
        _req(body={"s": f"Sec ghp_{'A' * 36}"}),
        _req(body={"s": "x" * 450}),
        _req(repo=None),
    ):
        pytest.raises(HTTPException, append_entry, agent, req)

    validate_status_transition("proposed", "accepted")
    pytest.raises(HTTPException, validate_status_transition, "retired", "accepted")
    pytest.raises(HTTPException, review_entry, agent, e.id, MemoryReviewRequest(review_status="accepted"))

    e_bb = append_entry(agent, _req(visibility="barb-only"))
    e_cf = append_entry(agent, _req(body={"s": "require human reviewer"}))
    assert review_entry(lib, e_cf.id, MemoryReviewRequest(review_status="accepted")).review_status == "quarantined"
    assert review_entry(lib, e.id, MemoryReviewRequest(review_status="accepted")).review_status == "accepted"
    f_pub = [x.id for x in list_entries(entry_type="lesson", repo="RD")]
    f_all = [x.id for x in list_entries(entry_type="lesson", repo="RD", include_barb_only=True)]
    assert e.id in f_pub and e_bb.id not in f_pub and e_bb.id in f_all

    store.append(_ent("mem_exp", expires_at=(now - timedelta(seconds=1)).isoformat()))
    assert e.id in (p_ids := [it.id for it in get_prompt_lessons(store=store, repo="RD")]) and "mem_exp" not in p_ids

    c, h = TestClient(app, raise_server_exceptions=False), {"X-Requested-With": "XMLHttpRequest"}
    app.dependency_overrides[require_principal] = lambda: _p("agent", "run-api-1")
    res = c.post(
        "/api/v1/memory", json={"entry_type": "lesson", "source_product": "d", "repo": "RD", "body": {}}, headers=h
    )
    assert res.status_code == 201 and res.json()["entry"]["review_status"] == "proposed"
    eid, rev = res.json()["entry"]["id"], {"review_status": "accepted"}
    assert c.patch(f"/api/v1/memory/{eid}/review", json=rev, headers=h).status_code == 403
    app.dependency_overrides[require_principal] = lambda: _p("lib")
    assert c.patch(f"/api/v1/memory/{eid}/review", json=rev, headers=h).status_code == 200
    entries = c.get("/api/v1/memory?type=lesson&repo=RD&review_status=accepted").json()["entries"]
    assert (
        any(it["id"] == eid for it in entries) and not c.get("/api/v1/memory?type=lesson&repo=Other").json()["entries"]
    )
    app.dependency_overrides.clear()
