"""Automatic Barb summaries are bounded, opt-in and sent only to the shared origin."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from identity import Principal
from staff import chat_memory_summary as summaries

pytestmark = pytest.mark.unit


def owner(*scopes):
    return Principal(id="human-owner", type="human", name="Owner", scopes=list(scopes))


def capsule(body=None):
    body = body or {"summary": "Agreed to test the runner pipeline.", "decisions": [], "open_items": ["Run canary."]}
    return "Reply.\n\n```staff-memory\n" + json.dumps(body) + "\n```"


def test_summary_instructions_require_both_opt_in_and_original_caller_scope(monkeypatch):
    monkeypatch.setenv("BARB_SHARED_MEMORY_AUTO_SUMMARIES", "1")
    monkeypatch.setenv("BARB_SHARED_MEMORY_URL", "https://desk.example")
    allowed = owner("memory.shared.summarize")
    assert summaries.summary_instruction("barb", allowed)
    assert summaries.summary_instruction("night-watch", allowed) is None
    assert summaries.summary_instruction("barb", owner("memory.read")) is None
    monkeypatch.delenv("BARB_SHARED_MEMORY_AUTO_SUMMARIES")
    assert summaries.summary_instruction("barb", allowed) is None


def test_valid_trailing_capsule_is_stripped_and_not_a_transcript():
    reply, digest = summaries.extract_summary(capsule())
    assert reply == "Reply."
    assert digest["summary"] == "Agreed to test the runner pipeline."
    assert "Reply." not in digest.values()


@pytest.mark.parametrize(
    "raw",
    [capsule()[:-3], capsule() + "\nMore prose.", capsule() + "\n" + capsule()],
)
def test_recognized_metadata_never_reappears_after_stream_filter_hides_it(raw):
    reply, digest = summaries.extract_summary(raw)
    assert reply == "Reply."
    assert digest is None


@pytest.mark.parametrize(
    "raw",
    [
        "```text\n" + capsule() + "\n```",
        "> " + capsule().replace("\n", "\n> "),
        capsule() + "\nMore prose.",
        capsule() + "\n" + capsule(),
        capsule({"summary": "x", "decisions": [], "open_items": [], "transcript": "private"}),
        capsule({"summary": "x" * 1001, "decisions": [], "open_items": []}),
        capsule({"summary": "x", "decisions": ["x"] * 4, "open_items": []}),
        "Reply.\n```staff-memory\nnot JSON\n```",
    ],
)
def test_quoted_nested_multiple_nontrailing_or_invalid_capsule_never_writes(raw):
    _, digest = summaries.extract_summary(raw)
    assert digest is None


def test_summary_secrets_are_redacted_before_network():
    _, digest = summaries.extract_summary(
        capsule(
            {
                "summary": "Key sk-" + "a" * 32,
                "decisions": ["Token ghs_" + "a" * 40],
                "open_items": [],
            }
        )
    )
    assert digest is not None
    assert "a" * 32 not in json.dumps(digest)


@pytest.mark.parametrize(
    "role,requester,opt_in",
    [
        ("night-watch", owner("memory.shared.summarize"), "1"),
        ("barb", owner("memory.read"), "1"),
        ("barb", owner("memory.shared.summarize"), "0"),
    ],
)
def test_writer_refuses_before_token_or_transport(monkeypatch, role, requester, opt_in):
    monkeypatch.setenv("BARB_SHARED_MEMORY_AUTO_SUMMARIES", opt_in)
    monkeypatch.setenv("BARB_SHARED_MEMORY_URL", "https://desk.example")
    monkeypatch.setattr(summaries, "_post_digest", AsyncMock(side_effect=AssertionError("network")))
    result = asyncio.run(
        summaries.save_summary(
            role,
            requester,
            "thread",
            "message",
            {
                "summary": "Done.",
                "decisions": [],
                "open_items": [],
            },
        )
    )
    assert result == "disabled"


def test_remote_failure_is_visible_status_without_local_fallback(monkeypatch):
    monkeypatch.setenv("BARB_SHARED_MEMORY_AUTO_SUMMARIES", "1")
    monkeypatch.setenv("BARB_SHARED_MEMORY_URL", "https://desk.example")
    monkeypatch.setattr(summaries, "_post_digest", AsyncMock(side_effect=TimeoutError))
    result = asyncio.run(
        summaries.save_summary(
            "barb",
            owner("memory.shared.summarize"),
            "thread",
            "message",
            {
                "summary": "Done.",
                "decisions": [],
                "open_items": [],
            },
        )
    )
    assert result == "unavailable"


@pytest.mark.asyncio
async def test_finish_records_only_status_and_preserves_message(monkeypatch, tmp_path):
    from staff.conversations import ConversationStore

    store = ConversationStore(tmp_path / "staff.sqlite3")
    thread = store.create_thread(title="Barb", role="barb")
    message = store.add_message(
        thread.id, author_kind="role", author="barb", body_md="Your reply.", meta={"metrics": {"provider": "codex"}}
    )
    writer = AsyncMock(return_value="saved")
    monkeypatch.setattr(summaries, "save_summary", writer)
    caller = owner("memory.shared.summarize")
    digest = {"summary": "We agreed to test.", "decisions": [], "open_items": []}
    await summaries.finish_summary(store, "barb", caller, thread.id, "user-message", message.id, digest)
    writer.assert_awaited_once_with("barb", caller, thread.id, "user-message", digest)
    updated = store.get_message(message.id)
    assert updated.body_md == "Your reply."
    assert updated.meta == {"metrics": {"provider": "codex"}, "shared_memory_summary": "saved"}
    assert "summary" not in updated.meta


@pytest.mark.asyncio
async def test_postprocessing_error_cannot_fail_completed_reply(monkeypatch):
    monkeypatch.setattr(summaries, "_finish_summary", AsyncMock(side_effect=OSError("unavailable")))
    await summaries.finish_summary(None, "barb", owner(), "thread", "user", "reply", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [201, 200, 403, 409, 302])
async def test_canonical_transport_uses_digest_only_and_rejects_errors(monkeypatch, status):
    captured = []
    original = httpx.AsyncClient
    payload = {
        "source_product": "dashboard",
        "conversation_id": "thread",
        "user_message_id": "message",
        "summary": "Agreed to test.",
        "decisions": [],
        "open_items": [],
    }

    def handler(request):
        captured.append(request)
        entry = {
            "id": "mem-test",
            "entry_type": "session_digest",
            "author_principal": "bridge",
            "source_product": "dashboard",
            "created_at": "2026-10-22T00:00:00Z",
            "body": payload,
        }
        return httpx.Response(status, json={"entry": entry, "replayed": status == 200})

    def client(**kwargs):
        assert kwargs == {"timeout": 2.0, "trust_env": False, "follow_redirects": False}
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(summaries.httpx, "AsyncClient", client)
    monkeypatch.setattr(summaries, "_read_bearer_token", lambda: "bridge-test-token")
    if status in {200, 201}:
        assert await summaries._post_digest("https://desk.example", payload) == (
            "replayed" if status == 200 else "saved"
        )
    else:
        with pytest.raises(httpx.HTTPStatusError):
            await summaries._post_digest("https://desk.example", payload)
    assert len(captured) == 1
    assert str(captured[0].url) == "https://desk.example/api/v1/memory/session-digest"
    assert json.loads(captured[0].content) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_successful_turn_writes_with_original_requester_and_stable_message_key(monkeypatch, tmp_path, resume):
    from staff import chat
    from staff.conversations import ConversationStore
    from staff.roles import RoleSpec

    monkeypatch.setenv("BARB_SHARED_MEMORY_AUTO_SUMMARIES", "1")
    monkeypatch.setenv("BARB_SHARED_MEMORY_URL", "https://desk.example")
    store = ConversationStore(tmp_path / "staff.sqlite3")
    thread = store.create_thread(
        title="Barb", role="barb", meta={"provider_sessions": {"claude": "session"}} if resume else {}
    )
    user = store.add_message(thread.id, author_kind="user", author="Owner", body_md="Please test.")
    reply = store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")
    role = RoleSpec(name="barb", title="Barb", summary="Secretary", instructions="Keep this persona.")
    monkeypatch.setattr(chat, "load_roles", lambda: {"barb": role})
    monkeypatch.setattr(chat, "build_knowledge_turn_block", lambda *_: None)
    monkeypatch.setattr(chat, "build_pending_proposals_block", lambda *_: None)
    for name in ("build_fleet_context_block", "build_issue_context_block", "build_shared_memory_context_block"):
        monkeypatch.setattr(chat, name, AsyncMock(return_value=None))
    monkeypatch.setattr(chat, "is_provider_healthy", lambda _: True)
    runner = chat.ChatTurnRunner(conv_store=store, pool=chat.ChatConcurrencyPool(max_concurrency=1, barb_reserved=0))
    digest = {"summary": "Agreed to test.", "decisions": [], "open_items": []}

    async def attempt(**kwargs):
        assert "staff-memory" in kwargs["prompt"]
        assert kwargs["requester"] is caller
        store.update_message(reply.id, delivery="complete", body_md="Testing next.")
        return chat.ChatTurnResult(ok=True, reply="Testing next.", memory_digest=digest)

    monkeypatch.setattr(runner, "_run_turn_attempt", attempt)
    writer = AsyncMock(return_value="saved")
    monkeypatch.setattr(summaries, "_post_digest", writer)
    caller = owner("memory.read", "memory.shared.summarize")
    result = await runner.execute_turn(thread.id, user.id, reply.id, "barb", provider="claude", requester=caller)
    assert result.ok
    payload = writer.await_args.args[1]
    assert writer.await_count == 1
    assert payload["conversation_id"] == thread.id and payload["user_message_id"] == user.id
    assert payload["reported_origin_principal"] == caller.id
    assert payload["summary"] == digest["summary"]
    assert "Please test." not in json.dumps(payload)
    assert store.get_message(reply.id).meta["shared_memory_summary"] == "saved"
