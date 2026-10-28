"""Business logic for assistant memory append and context pack."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from identity import Principal, format_caller
from memory.contract import (
    CONTEXT_DIGEST_LIMIT,
    CONTEXT_TASK_LIMIT,
    DEFAULT_LESSON_TTL_DAYS,
    MAX_LESSON_TTL_DAYS,
    enforce_entry_size,
    validate_status_transition,
)
from memory.expiry import expiry_is_future
from memory.models import (
    MemoryAppendRequest,
    MemoryContextPack,
    MemoryEntry,
    MemoryReviewRequest,
    SessionDigestAppendRequest,
)
from memory.redact import contains_secret, redact_memory_text
from memory.store import AssistantMemoryStore, get_memory_store, new_entry_id
from priorities.directives import get_directives
from routers.staff_schedule import current_holds
from staff import store as run_stores
from staff.barb_lease_fence import is_barb_seat
from staff.lesson_provenance import authenticated_lesson_run, completed_run_for_review
from staff.store import _now  # noqa: PLC2701


def _redact_body(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in body.items():
        if isinstance(value, str):
            out[key] = redact_memory_text(value)
        elif isinstance(value, list):
            out[key] = [redact_memory_text(str(v)) if isinstance(v, str) else v for v in value]
        else:
            out[key] = value
    return out


def is_barb_principal(p: Principal | None) -> bool:
    """A Barb seat or an admin: who may read Barb-only memory."""
    return is_barb_seat(p) or bool(p and "admin" in set(p.roles + p.scopes))


def is_librarian_principal(p: Principal | None) -> bool:
    roles = set(p.roles + p.scopes) if p else set()
    return bool(p and (p.id in {"librarian", "fleet-librarian"} or roles & {"librarian", "memory.review", "admin"}))


_FLEET_CONFLICT_RE = re.compile(
    r"\b(request(?:ing)? human review|assign(?:ing)? fleet-operator|human-approval|require human reviewer|"
    r"pay-per-use|OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|create draft pr|stack(?:ed)? prs?)\b",
    re.I,
)


def validate_lesson_acceptance(entry: MemoryEntry, store: AssistantMemoryStore) -> str:
    text = " ".join(str(v) for v in entry.body.values() if isinstance(v, (str, int, float)))
    if _FLEET_CONFLICT_RE.search(text) or entry.body.get("conflicts_with_fleet_rules"):
        return "quarantined"
    top = str(entry.body.get("topic") or entry.body.get("rule") or "").strip().lower()
    for o in store.list_entries(entry_type="lesson", review_status="accepted", repo=entry.repo):
        ot = str(o.body.get("topic") or o.body.get("rule") or "").strip().lower()
        clash = top and top == ot and entry.body.get("stance") != o.body.get("stance")
        if o.id != entry.id and (entry.body.get("conflicts_with") == o.id or clash):
            return "quarantined"
    return "accepted"


def _validate_and_resolve_expires_at(expires_at: str | None) -> str:
    now = datetime.now(UTC)
    if not expires_at:
        return (now + timedelta(days=DEFAULT_LESSON_TTL_DAYS)).isoformat()
    try:
        dt = datetime.fromisoformat(expires_at)
        dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        if dt <= now or dt > now + timedelta(days=MAX_LESSON_TTL_DAYS):
            raise ValueError
        return dt.isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid expires_at timestamp") from exc


def append_entry(principal: Principal, req: MemoryAppendRequest) -> MemoryEntry:
    expires_at: str | None = req.expires_at
    source_run: str | None
    repo: str | None
    review_status: str | None
    visibility: str | None
    if req.entry_type == "lesson":
        if contains_secret(req.body):
            raise HTTPException(status_code=422, detail="secret detected in lesson entry")
        enforce_entry_size(req.body, "lesson")
        if req.review_status and req.review_status != "proposed":
            if not is_librarian_principal(principal):
                raise HTTPException(status_code=403, detail="agent principal cannot set non-proposed review_status")
            raise HTTPException(status_code=422, detail="new lessons must be proposed before review")
        run = authenticated_lesson_run(principal, req.source_run, req.repo or req.body.get("repo"))
        if "repo" in req.body and req.body["repo"] != run.repo:
            raise HTTPException(status_code=403, detail="lesson body repository does not match stored run")
        source_run, repo = run.id, run.repo
        review_status, visibility = req.review_status or "proposed", req.visibility or "fleet"
        expires_at = _validate_and_resolve_expires_at(req.expires_at)
    else:
        source_run, repo = req.source_run, req.repo
        review_status, visibility = req.review_status, req.visibility

    body = _redact_body(req.body)
    if req.entry_type == "lesson":
        body["standards_version"] = run.standards_version
    enforce_entry_size(body, req.entry_type)
    store = get_memory_store()
    if req.supersedes_id:
        superseded = store.get(req.supersedes_id)
        if superseded is None:
            raise HTTPException(status_code=404, detail="supersedes_id not found")
        if superseded.entry_type == "lesson":
            raise HTTPException(status_code=422, detail="lessons cannot be superseded through generic append")
    entry = MemoryEntry(
        id=new_entry_id(),
        entry_type=req.entry_type,
        author_principal=format_caller(principal),
        source_product=req.source_product.strip(),
        tags=[t.strip()[:64] for t in req.tags if t.strip()],
        body=body,
        created_at=_now(),
        expires_at=expires_at,
        supersedes_id=req.supersedes_id,
        repo=repo,
        review_status=review_status,
        source_run=source_run,
        visibility=visibility,
    )
    try:
        return store.append(entry)
    except ValueError as exc:
        if str(exc) == "store_size_cap_exceeded":
            raise HTTPException(status_code=507, detail="assistant memory store size cap exceeded") from exc
        raise


def review_entry(principal: Principal, entry_id: str, req: MemoryReviewRequest) -> MemoryEntry:
    if not is_librarian_principal(principal):
        raise HTTPException(status_code=403, detail="only librarian principal can review memory entries")
    store = get_memory_store()
    if not (entry := store.get(entry_id)):
        raise HTTPException(status_code=404, detail="memory entry not found")
    if entry.entry_type != "lesson":
        raise HTTPException(status_code=422, detail="only lessons can be reviewed")
    if req.review_status == "promoted":
        raise HTTPException(status_code=422, detail="promotion requires trusted merged-promotion evidence")
    validate_status_transition(entry.review_status, req.review_status)
    target = validate_lesson_acceptance(entry, store) if req.review_status == "accepted" else req.review_status
    if target == "accepted":
        if not expiry_is_future(entry.expires_at, _now()):
            raise HTTPException(status_code=409, detail="lesson proposal expired before review")
        if entry.source_run and completed_run_for_review(run_stores.get_store(), entry.source_run) is None:
            raise HTTPException(status_code=409, detail="lesson run has not reached authoritative completion")
    exp = _validate_and_resolve_expires_at(req.expires_at) if req.expires_at or target == "accepted" else None
    acc = _now() if target == "accepted" else entry.accepted_at
    kw = {"review_status": target, "reviewed_by": format_caller(principal), "accepted_at": acc, "expires_at": exp}
    try:
        return store.update_review(entry_id, expected_review_status=entry.review_status or "proposed", **kw)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="lesson review is stale; reload and retry") from exc


def append_session_digest(principal: Principal, req: SessionDigestAppendRequest) -> tuple[MemoryEntry, bool]:
    """Append a bounded digest once per authenticated writer and source turn."""
    caller = format_caller(principal)
    canonical = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    stable_identity = json.dumps(
        [caller, req.source_product, req.conversation_id, req.user_message_id],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    idempotency_key = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()
    body = {
        "conversation_id": req.conversation_id,
        "user_message_id": req.user_message_id,
        "reported_origin_principal": (
            redact_memory_text(req.reported_origin_principal) if req.reported_origin_principal is not None else None
        ),
        "summary": redact_memory_text(req.summary),
        "decisions": [redact_memory_text(item) for item in req.decisions],
        "open_items": [redact_memory_text(item) for item in req.open_items],
    }
    enforce_entry_size(body, "session_digest")
    entry = MemoryEntry(
        id=new_entry_id(),
        entry_type="session_digest",
        author_principal=caller,
        source_product=req.source_product,
        tags=[],
        body=body,
        created_at=_now(),
    )
    try:
        return get_memory_store().append_idempotent(entry, idempotency_key=idempotency_key, fingerprint=fingerprint)
    except ValueError as exc:
        if str(exc) == "idempotency_key_reused":
            raise HTTPException(status_code=409, detail="session digest key was reused with different content") from exc
        if str(exc) == "store_size_cap_exceeded":
            raise HTTPException(status_code=507, detail="assistant memory store size cap exceeded") from exc
        raise


def list_entries(**kwargs: Any) -> list[MemoryEntry]:
    return get_memory_store().list_entries(include_superseded=False, **kwargs)


def _open_tasks(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    closed = {"done", "closed", "cancelled", "complete", "completed"}
    open_entries = [e for e in entries if str(e.body.get("status", "open")).lower() not in closed]
    return open_entries[:CONTEXT_TASK_LIMIT]


def get_prompt_lessons(
    store: AssistantMemoryStore | None = None,
    repo: str | None = None,
    principal: Principal | None = None,
    limit: int = 10,
) -> list[MemoryEntry]:
    return (store or get_memory_store()).list_entries(
        entry_type="lesson",
        repo=repo,
        review_status="accepted",
        include_barb_only=is_barb_principal(principal),
        active_only=True,
        limit=limit,
    )


def build_context_pack() -> MemoryContextPack:
    store = get_memory_store()
    digests = store.list_entries(entry_type="session_digest", limit=CONTEXT_DIGEST_LIMIT)
    tasks = _open_tasks(store.list_entries(entry_type="task", limit=CONTEXT_TASK_LIMIT * 2))
    directives = [d.to_dict() for d in get_directives().active()]
    holds = current_holds()
    return MemoryContextPack(
        session_digests=digests,
        open_tasks=tasks,
        directives=directives,
        holds=holds,
    )
