"""Tests for backend/orchestration_audit.py — issue #386."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import orchestration_audit as oa
import pytest

# ---------------------------------------------------------------------------
# load_orchestration_audit — no file
# ---------------------------------------------------------------------------


def test_load_audit_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", tmp_path / "nonexistent.json")
    result = oa.load_orchestration_audit()
    assert result == []


# ---------------------------------------------------------------------------
# load_orchestration_audit — NDJSON file
# ---------------------------------------------------------------------------


def test_load_audit_ndjson(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "audit.json"
    entries = [{"action": "dispatch", "ts": "2026-04-01T00:00:00Z"}, {"action": "cancel", "ts": "2026-04-01T01:00:00Z"}]
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", p)
    result = oa.load_orchestration_audit()
    assert len(result) == 2
    assert result[-1]["action"] == "cancel"


def test_load_audit_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "audit.json"
    entries = [{"n": i} for i in range(20)]
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", p)
    result = oa.load_orchestration_audit(limit=5)
    assert len(result) == 5


def test_load_audit_filters_by_principal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "audit.json"
    entries = [
        {"principal": "alice", "action": "start"},
        {"principal": "bob", "action": "stop"},
        {"principal": "alice", "action": "cancel"},
    ]
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", p)
    result = oa.load_orchestration_audit(principal="alice")
    assert all(r.get("principal") == "alice" for r in result)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# _migrate_audit_to_ndjson_if_needed — legacy JSON array migration
# ---------------------------------------------------------------------------


def test_migrate_legacy_json_array(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "audit.json"
    legacy = [{"action": "old-a"}, {"action": "old-b"}]
    p.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", p)
    oa._migrate_audit_to_ndjson_if_needed()
    # File should now be NDJSON (first char should not be '[')
    content = p.read_text(encoding="utf-8")
    assert not content.startswith("[")
    lines = [l for l in content.splitlines() if l.strip()]
    assert len(lines) == 2


def test_migrate_noop_on_ndjson(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "audit.json"
    p.write_text('{"action": "already-ndjson"}\n', encoding="utf-8")
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", p)
    oa._migrate_audit_to_ndjson_if_needed()
    content = p.read_text(encoding="utf-8")
    assert not content.startswith("[")


# ---------------------------------------------------------------------------
# get_audit_log_corrupt_total
# ---------------------------------------------------------------------------


def test_corrupt_total_is_non_negative() -> None:
    total = oa.get_audit_log_corrupt_total()
    assert isinstance(total, int)
    assert total >= 0


# ---------------------------------------------------------------------------
# append_orchestration_event — writes to file
# ---------------------------------------------------------------------------


def test_append_orchestration_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "audit.json"
    monkeypatch.setattr(oa, "ORCHESTRATION_AUDIT_PATH", p)

    async def run() -> None:
        await oa.append_orchestration_audit({"action": "test-append", "principal": "agent:claude"})

    asyncio.run(run())
    assert p.exists()
    lines = [line for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "test-append"
