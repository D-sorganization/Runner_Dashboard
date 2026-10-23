from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from memory.models import MemoryEntry
from memory.service import get_prompt_lessons
from memory.store import AssistantMemoryStore
from staff.lessons import select_repo_lessons

pytestmark = pytest.mark.unit


def _lesson(store: AssistantMemoryStore, entry_id: str, expiry: str | None, created: str) -> None:
    store.append(
        MemoryEntry(
            id=entry_id,
            entry_type="lesson",
            author_principal="librarian",
            source_product="fixture",
            repo="UpstreamDrift",
            review_status="accepted",
            visibility="fleet",
            body={"text": "Use the checked contract."},
            created_at=created,
            expires_at=expiry,
        )
    )


@pytest.mark.parametrize("selector", ["prompt", "repo"])
@pytest.mark.parametrize("kind", ["expired-positive-offset", "future-negative-offset", "malformed", "naive"])
def test_expiry_uses_instants_in_both_selectors(tmp_path: Path, selector: str, kind: str) -> None:
    now = datetime.now(UTC)
    expiry = {
        "expired-positive-offset": (now - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=14))).isoformat(),
        "future-negative-offset": (now + timedelta(minutes=5)).astimezone(timezone(timedelta(hours=-12))).isoformat(),
        "malformed": "not-a-timestamp",
        "naive": (now + timedelta(days=1)).replace(tzinfo=None).isoformat(),
    }[kind]
    store = AssistantMemoryStore(tmp_path / "memory.sqlite3")
    try:
        _lesson(store, "mem_offset", expiry, now.isoformat())
        if selector == "prompt":
            ids = [entry.id for entry in get_prompt_lessons(store=store, repo="UpstreamDrift")]
        else:
            ids = list(select_repo_lessons("UpstreamDrift", memory_store=store).injected_ids)
        assert ("mem_offset" in ids) == (kind == "future-negative-offset")
    finally:
        store._db.close()


def test_filtering_expiry_before_limit_keeps_valid_lessons(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    store = AssistantMemoryStore(tmp_path / "memory.sqlite3")
    try:
        _lesson(store, "mem_good", (now + timedelta(days=1)).isoformat(), (now - timedelta(hours=1)).isoformat())
        expired = (now - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=14))).isoformat()
        _lesson(store, "mem_bad", expired, now.isoformat())

        assert [entry.id for entry in get_prompt_lessons(store=store, repo="UpstreamDrift", limit=1)] == ["mem_good"]
    finally:
        store._db.close()
