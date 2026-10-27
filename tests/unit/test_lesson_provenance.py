"""Lesson proposals cannot manufacture a staff run's provenance."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException
from identity import Principal
from memory.models import MemoryAppendRequest
from memory.service import append_entry
from memory.store import get_memory_store, reset_memory_store
from staff import store as run_stores
from staff import tokens
from staff.lessons import file_proposed_lessons
from staff.store import RunRecord, RunStore

pytestmark = pytest.mark.unit


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    reset_memory_store()
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord("run", "role", "claude", None, "machine", "repo", "prompt", "", "test",
                    status="running", requested_by="operator", standards_version="v1")
    store.create_run(rec)
    monkeypatch.setattr(run_stores, "get_store", lambda: store)
    monkeypatch.setattr(tokens, "_active_run_tokens", {"run": "staff:role:run"})
    principal = Principal(id="staff:role:run", type="bot", name="test", roles=["staff-run:run"], scopes=["memory.write"])
    yield store, rec, principal
    store.close()
    reset_memory_store()


def request(**kwargs) -> MemoryAppendRequest:
    return MemoryAppendRequest(**({"entry_type": "lesson", "source_product": "test", "repo": "repo",
                                  "body": {"text": "exact"}} | kwargs))


@pytest.mark.parametrize("case", ["generic", "unbound", "marker", "multiple_markers", "role", "missing_run",
                                  "repo", "body_repo", "source", "standards", "requester", "completed"])
def test_api_rejects_fabricated_or_stale_provenance(context, case: str) -> None:
    store, _, principal = context
    kwargs = {}
    if case == "generic":
        principal = principal.model_copy(update={"id": "ordinary", "roles": []})
        kwargs["source_run"] = "run"
    elif case == "unbound":
        tokens._active_run_tokens.clear()
    elif case == "marker":
        principal = principal.model_copy(update={"roles": []})
    elif case == "multiple_markers":
        principal = principal.model_copy(update={"roles": ["staff-run:run", "staff-run:other"]})
    elif case == "role":
        store.update_run("run", role="other")
    elif case == "missing_run":
        store._conn.execute("DELETE FROM runs")
    elif case == "repo":
        kwargs["repo"] = "other"
    elif case == "body_repo":
        kwargs["body"] = {"text": "exact", "repo": "other"}
    elif case == "source":
        kwargs["source_run"] = "other"
    elif case == "standards":
        store.update_run("run", standards_version="")
    elif case == "requester":
        store.update_run("run", requested_by="")
    elif case == "completed":
        store.update_run("run", status="succeeded")
    with pytest.raises(HTTPException) as exc:
        append_entry(principal, request(**kwargs))
    assert exc.value.status_code == 403
    assert get_memory_store().list_entries(entry_type="lesson") == []


def test_api_valid_binding_derives_standard_from_store(context) -> None:
    _, _, principal = context
    entry = append_entry(principal, request(body={"text": "exact", "standards_version": "forged"}))
    assert entry.source_run == "run" and entry.repo == "repo"
    assert entry.body["standards_version"] == "v1"


@pytest.mark.parametrize("case", ["missing", "failed", "exit", "role", "repo", "requester", "standards"])
def test_internal_filing_reloads_and_checks_authoritative_run(context, case: str) -> None:
    store, rec, _ = context
    store.update_run("run", status="succeeded", exit_code=0)
    caller = replace(rec, status="succeeded", exit_code=0)
    if case == "missing":
        store._conn.execute("DELETE FROM runs")
    else:
        field, value = {"failed": ("status", "failed"), "exit": ("exit_code", 1), "role": ("role", "other"),
                        "repo": ("repo", "other"), "requester": ("requested_by", "other"),
                        "standards": ("standards_version", "")}[case]
        store.update_run("run", **{field: value})
    assert file_proposed_lessons(store, caller, events=[{"kind": "assistant", "text": "STAFF_LESSON: exact"}]) == []
    assert get_memory_store().list_entries(entry_type="lesson") == []


def test_internal_valid_run_preserves_legacy_parser_and_reads(context) -> None:
    store, rec, _ = context
    store.update_run("run", status="succeeded", exit_code=0)
    caller = replace(rec, status="succeeded", exit_code=0)
    ids = file_proposed_lessons(store, caller, events=[{"kind": "assistant", "text": "STAFF_LESSON: exact"}])
    assert len(ids) == 1
    entry = get_memory_store().get(ids[0])
    assert entry.source_run == "run" and entry.author_principal == "operator"
