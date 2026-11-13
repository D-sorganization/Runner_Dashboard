"""Chat failure recording when the placeholder is still streaming (#1341)."""

from __future__ import annotations

import pytest

from staff.chat_failures import record_chat_failure_if_pending
from staff.conversations import ConversationStore


@pytest.mark.asyncio
async def test_records_failure_when_placeholder_still_streaming(tmp_path) -> None:
    store = ConversationStore(tmp_path / "staff.db")
    thread = store.create_thread(role="e2e-analyst", kind="direct", title="t")
    placeholder = store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="e2e-analyst",
        kind="text",
        body_md="",
        delivery="streaming",
    )

    await record_chat_failure_if_pending(
        store,
        thread.id,
        placeholder.id,
        actor="e2e-analyst",
        failure_class="auth_expired",
        retryable=False,
        detail="Run login",
        error="auth",
    )

    updated = store.get_message(placeholder.id)
    assert updated is not None
    assert updated.delivery == "failed"
    assert updated.meta.get("failure_class") == "auth_expired"
