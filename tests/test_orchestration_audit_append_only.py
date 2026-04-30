"""Tests for append-only NDJSON orchestration audit log (issue #412).

Acceptance criteria:
1. Write 5,000 entries -> all 5,000 readable; limit=100 returns latest 100.
2. Append uses O_APPEND, no read-modify-write cycle.
3. Legacy single-JSON-array files migrate to NDJSON on first read.
4. Corrupt lines increment audit_log_corrupt_total counter.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _patched_audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the audit path constant to a temp file."""
    import server  # noqa: PLC0415

    audit_path = tmp_path / "orchestration_audit.json"
    monkeypatch.setattr(server, "_ORCHESTRATION_AUDIT_PATH", audit_path)
    monkeypatch.setattr(server, "_audit_log_corrupt_total", 0)
    return audit_path


def test_5000_entries_readable_no_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing 5,000 entries leaves all 5,000 readable (no 1000-cap)."""
    import server  # noqa: PLC0415

    _patched_audit_path(tmp_path, monkeypatch)

    async def write_5000() -> None:
        for i in range(5000):
            await server._append_orchestration_audit({"event_id": f"e{i}", "i": i})

    asyncio.run(write_5000())

    all_entries = server._load_orchestration_audit(limit=10_000)
    assert len(all_entries) == 5000, f"expected 5000 entries, got {len(all_entries)}"
    assert all_entries[0]["i"] == 0
    assert all_entries[-1]["i"] == 4999


def test_limit_returns_latest_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """limit=100 returns the latest 100 entries, not the first 100."""
    import server  # noqa: PLC0415

    _patched_audit_path(tmp_path, monkeypatch)

    async def write_5000() -> None:
        for i in range(5000):
            await server._append_orchestration_audit({"event_id": f"e{i}", "i": i})

    asyncio.run(write_5000())

    last_100 = server._load_orchestration_audit(limit=100)
    assert len(last_100) == 100
    assert last_100[0]["i"] == 4900
    assert last_100[-1]["i"] == 4999


def test_legacy_json_array_migrated_on_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing JSON-array audit file is migrated to NDJSON on first load."""
    import server  # noqa: PLC0415

    audit_path = _patched_audit_path(tmp_path, monkeypatch)

    legacy_entries = [{"event_id": f"old-{i}", "i": i} for i in range(50)]
    audit_path.write_text(json.dumps(legacy_entries))

    loaded = server._load_orchestration_audit(limit=100)
    assert len(loaded) == 50
    assert loaded[0]["event_id"] == "old-0"
    assert loaded[-1]["event_id"] == "old-49"

    # File should now be NDJSON (50 lines, each a JSON object)
    raw = audit_path.read_text()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 50
    for line in lines:
        entry = json.loads(line)
        assert "event_id" in entry


def test_corrupt_line_increments_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON line increments _audit_log_corrupt_total but does not abort the read."""
    import server  # noqa: PLC0415

    audit_path = _patched_audit_path(tmp_path, monkeypatch)

    audit_path.write_text('{"event_id":"good-1","i":1}\nthis is not json\n{"event_id":"good-2","i":2}\n')

    before = server.get_audit_log_corrupt_total()
    loaded = server._load_orchestration_audit(limit=10)
    after = server.get_audit_log_corrupt_total()

    assert len(loaded) == 2  # corrupt line skipped
    assert {e["event_id"] for e in loaded} == {"good-1", "good-2"}
    assert after - before == 1


def test_append_does_not_rewrite_existing_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Appending entry N+1 must not modify entries 1..N (no read-modify-write)."""
    import server  # noqa: PLC0415

    audit_path = _patched_audit_path(tmp_path, monkeypatch)

    async def write_one(payload: dict) -> None:
        await server._append_orchestration_audit(payload)

    asyncio.run(write_one({"event_id": "first"}))
    raw_after_first = audit_path.read_bytes()

    asyncio.run(write_one({"event_id": "second"}))
    raw_after_second = audit_path.read_bytes()

    # The second file must START with the first file's bytes verbatim (append-only).
    assert raw_after_second.startswith(raw_after_first), "append must not rewrite existing lines"


def test_principal_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """principal filter returns only entries matching that principal."""
    import server  # noqa: PLC0415

    _patched_audit_path(tmp_path, monkeypatch)

    async def seed() -> None:
        for i in range(20):
            who = "alice" if i % 2 == 0 else "bob"
            await server._append_orchestration_audit({"event_id": f"e{i}", "principal": who})

    asyncio.run(seed())

    alice_entries = server._load_orchestration_audit(limit=50, principal="alice")
    bob_entries = server._load_orchestration_audit(limit=50, principal="bob")

    assert len(alice_entries) == 10
    assert len(bob_entries) == 10
    assert all(e["principal"] == "alice" for e in alice_entries)
    assert all(e["principal"] == "bob" for e in bob_entries)
