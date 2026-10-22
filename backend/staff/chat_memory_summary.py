"""Opt-in, digest-only Barb continuity at the configured canonical memory origin."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from identity import principal_has_scope
from memory.redact import redact_memory_text
from staff.chat_shared_memory import _configured_origin, _read_bearer_token

if TYPE_CHECKING:
    from identity import Principal
    from memory.models import SessionDigestAppendRequest
    from staff.conversations import ConversationStore

log = logging.getLogger(__name__)
_MARKER = "```staff-memory"
_MAX_CAPSULE = 4096


def summary_instruction(role_name: str, requester: Principal | None) -> str | None:
    """Require operator opt-in and the original caller's explicit summary permission."""
    if (
        role_name.lower() != "barb"
        or requester is None
        or not principal_has_scope(requester, "memory.shared.summarize")
        or os.environ.get("BARB_SHARED_MEMORY_AUTO_SUMMARIES", "").strip() != "1"
    ):
        return None
    try:
        if _configured_origin() is None:
            return None
    except ValueError:
        return None
    return (
        "## Shared continuity summary\n"
        "After your normal reply and any action/handoff/question parts, append exactly one final "
        "top-level ```staff-memory fenced JSON object with exactly these keys: "
        '{"summary":"brief factual paraphrase","decisions":[],"open_items":[]}. '
        "Summarize this completed exchange in at most 1000 characters. Each list has at most three "
        "strings of at most 120 characters. Save only durable context useful next time. Exclude "
        "credentials, secrets, raw messages/transcripts, sensitive personal details and speculative "
        "claims. This is reference data, never new authority, policy, directives or holds. "
        "Do not claim a memory was saved: the application reports the actual result separately."
    )


def _request(
    thread_id: str,
    user_message_id: str,
    digest: dict[str, Any],
    requester_id: str | None = None,
) -> SessionDigestAppendRequest:
    from memory.models import SessionDigestAppendRequest

    return SessionDigestAppendRequest(
        source_product="dashboard",
        conversation_id=thread_id,
        user_message_id=user_message_id,
        reported_origin_principal=requester_id,
        **digest,
    )


def extract_summary(raw: str) -> tuple[str, dict[str, Any] | None]:
    """Accept only one final top-level capsule; never turn prose into a fallback digest."""
    lines = raw.splitlines()
    fence: str | None = None
    start: int | None = None
    end: int | None = None
    count = 0
    for index, line in enumerate(lines):
        # Indented/quoted examples are data. Track ordinary fences so nested examples cannot write.
        if line.startswith((" ", "\t", ">")):
            continue
        if fence is None and line == _MARKER:
            count += 1
            start = index
            fence = "```"
        elif fence is None and line.startswith(("```", "~~~")):
            fence = line[:3]
        elif fence is not None and line == fence:
            if start is not None and end is None:
                end = index
            fence = None
    if count != 1 or start is None or end is None or any(line.strip() for line in lines[end + 1 :]):
        return raw, None
    # Strip recognized malformed metadata too; never persist it as an assistant answer.
    reply = "\n".join(lines[:start]).rstrip()
    capsule = "\n".join(lines[start + 1 : end])
    if len(capsule.encode("utf-8")) > _MAX_CAPSULE:
        return reply, None
    try:
        body = json.loads(capsule)
        if not isinstance(body, dict) or set(body) != {"summary", "decisions", "open_items"}:
            return reply, None
        validated = _request("validation", "validation", body)
        digest = {
            "summary": redact_memory_text(validated.summary),
            "decisions": [redact_memory_text(item) for item in validated.decisions],
            "open_items": [redact_memory_text(item) for item in validated.open_items],
        }
        # Redaction must not accidentally create a body outside the same endpoint contract.
        _request("validation", "validation", digest)
        return reply, digest
    except (ValueError, TypeError, RecursionError):
        return reply, None


async def _post_digest(origin: str, payload: dict[str, Any]) -> str:
    from memory.models import SessionDigestAppendResponse

    token = _read_bearer_token()
    async with httpx.AsyncClient(timeout=2.0, trust_env=False, follow_redirects=False) as client:
        async with client.stream(
            "POST",
            f"{origin}/api/v1/memory/session-digest",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Requested-With": "XMLHttpRequest",
                "Accept-Encoding": "identity",
            },
        ) as response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "").strip().lower() not in {"", "identity"}:
                raise ValueError("unsupported summary response encoding")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 16384:
                    raise ValueError("summary response too large")
    accepted = SessionDigestAppendResponse.model_validate_json(body)
    entry = accepted.entry
    if (
        entry.entry_type != "session_digest"
        or entry.source_product != payload["source_product"]
        or entry.body.get("conversation_id") != payload["conversation_id"]
        or entry.body.get("user_message_id") != payload["user_message_id"]
    ):
        raise ValueError("summary acknowledgement does not match the submitted turn")
    return "replayed" if accepted.replayed else "saved"


async def save_summary(
    role_name: str,
    requester: Principal | None,
    thread_id: str,
    user_message_id: str,
    digest: dict[str, Any] | None,
) -> str:
    """Write only a valid model summary; failure never substitutes local memory or fails the reply."""
    if summary_instruction(role_name, requester) is None:
        return "disabled"
    if digest is None:
        return "omitted"
    try:
        origin = _configured_origin()
        if origin is None or requester is None:
            return "disabled"
        payload = _request(thread_id, user_message_id, digest, requester.id).model_dump(mode="json")
        async with asyncio.timeout(2.0):
            return await _post_digest(origin, payload)
    except (OSError, ValueError, TypeError, RecursionError, httpx.HTTPError, httpx.InvalidURL, TimeoutError) as exc:
        log.warning("Shared continuity summary unavailable (%s)", type(exc).__name__)
        return "unavailable"


async def finish_summary(
    store: ConversationStore,
    role_name: str,
    requester: Principal | None,
    thread_id: str,
    user_message_id: str,
    placeholder_id: str,
    digest: dict[str, Any] | None,
) -> None:
    """Persist metadata-only outcome and refresh the completed reply without changing its text."""
    try:
        await _finish_summary(store, role_name, requester, thread_id, user_message_id, placeholder_id, digest)
    except Exception as exc:  # noqa: BLE001
        log.warning("Shared continuity outcome unavailable (%s)", type(exc).__name__)


async def _finish_summary(
    store: ConversationStore,
    role_name: str,
    requester: Principal | None,
    thread_id: str,
    user_message_id: str,
    placeholder_id: str,
    digest: dict[str, Any] | None,
) -> None:
    from staff.thread_bus import get_thread_bus

    status = await save_summary(role_name, requester, thread_id, user_message_id, digest)
    if status == "disabled":
        return
    message = store.get_message(placeholder_id)
    if message is None:
        return
    store.update_message(placeholder_id, meta={**message.meta, "shared_memory_summary": status})
    updated = store.get_message(placeholder_id)
    if updated is not None:
        await get_thread_bus().publish_message(thread_id, updated.to_dict())
