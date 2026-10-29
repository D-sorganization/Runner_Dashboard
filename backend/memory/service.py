"""Business logic for assistant memory append and context pack."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from identity import Principal, format_caller, principal_has_scope
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
from staff.lesson_locator import locate_lesson_evidence, verify_lesson_evidence
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
        if not principal_has_scope(principal, "memory.write"):
            raise HTTPException(
                status_code=403,
                detail={"error": "Authorization failed", "required_scope": "memory.write", "principal": principal.id},
            )
        source_run, repo = req.source_run, req.repo
        review_status, visibility = req.review_status, req.visibility

    body = _redact_body(req.body)
    if req.entry_type == "lesson":
        body["standards_version"] = run.standards_version
        body.pop("locator", None)
        text = body.get("text")
        if isinstance(text, str) and text:
            try:
                locator = locate_lesson_evidence(run_stores.get_store(), run.id, run.repo, text)
            except ValueError:
                locator = None
            if locator is not None:
                body["locator"] = locator
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
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="supersedes_id not found") from exc
    except ValueError as exc:
        if str(exc) == "stale_supersession_conflict":
            raise HTTPException(status_code=409, detail="supersedes_id was already superseded") from exc
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
        if not _lesson_evidence_still_verifies(entry):
            raise HTTPException(status_code=409, detail="lesson evidence locator no longer verifies")
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


_CLOSED_TASK_STATUSES: frozenset[str] = frozenset({"done", "closed", "cancelled", "complete", "completed"})
_WILDCARD_SCOPES: frozenset[str] = frozenset({"", "*", "fleet", "public", "shared", "all"})
_PUBLIC_DATA_CLASSES: frozenset[str] = frozenset({"", "public", "fleet", "shared"})
_BASELINE_API_SCOPES: frozenset[str] = frozenset(
    "memory.read memory.write memory.review memory.shared.summarize fleet.read fleet.maintain fleet.control".split()
)
_PRINCIPAL_KEYS = ("principal", "target_principal", "owner_principal", "allowed_principals")
_SCOPE_KEYS = ("scope", "scopes", "required_scope", "target_scope")
_DATA_CLASS_KEYS = ("data_class", "sensitivity", "classification", "level", "visibility")


def _open_tasks(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    open_entries = [e for e in entries if str(e.body.get("status", "open")).lower() not in _CLOSED_TASK_STATUSES]
    return open_entries[:CONTEXT_TASK_LIMIT]


def _as_token_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip().lower()] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v).strip().lower() for v in value if isinstance(v, str) and str(v).strip()]
    return []


def _caller_access_profile(
    principal: Principal | None,
) -> tuple[bool, bool, frozenset[str], frozenset[str], frozenset[str] | None, frozenset[str]]:
    if principal is None:
        return False, False, frozenset(), frozenset(), frozenset(), _PUBLIC_DATA_CLASSES
    raw_roles = [r.strip().lower() for r in principal.roles if isinstance(r, str) and r.strip()]
    raw_scopes = [s.strip().lower() for s in principal.scopes if isinstance(s, str) and s.strip()]
    is_admin = "admin" in raw_roles or "admin" in raw_scopes
    is_barb = is_barb_principal(principal)
    pid = principal.id.strip().lower()
    caller_ids = frozenset({pid, format_caller(principal).strip().lower(), f"principal:{pid}", f"agent:{pid}"})
    scope_tokens: set[str] = set(caller_ids) | {r for r in raw_roles if r != "bot"}
    explicit_repos: set[str] = set()
    allowed_dcs: set[str] = set(_PUBLIC_DATA_CLASSES)
    for s in raw_scopes:
        if s not in _BASELINE_API_SCOPES:
            scope_tokens.add(s)
        for prefix in ("scope:", "memory.read:", "memory:"):
            if s.startswith(prefix) and (sub := s.removeprefix(prefix).strip()):
                scope_tokens.add(sub)
        if s.startswith("repo:") and (repo_name := s.removeprefix("repo:").strip()) and repo_name != "*":
            explicit_repos.add(repo_name)
            scope_tokens.add(repo_name)
        for dc_prefix in ("data_class:", "memory.read.", "sensitivity:", "classification:"):
            if s.startswith(dc_prefix) and (dc_val := s.removeprefix(dc_prefix).strip()):
                allowed_dcs.add(dc_val)
    if is_barb:
        scope_tokens.update({"barb", "barb-only"})
    if is_admin:
        allowed_dcs.update({"internal", "confidential", "confidential_work"})
    allowed_repos = None if (is_admin or is_barb) else frozenset(explicit_repos)
    return is_admin, is_barb, caller_ids, frozenset(scope_tokens), allowed_repos, frozenset(allowed_dcs)


def _entry_data_classes(entry: MemoryEntry) -> set[str]:
    classes: set[str] = set()
    vis = (entry.visibility or "").strip().lower()
    if vis and vis not in {"fleet", "barb-only"}:
        classes.add(vis)
    body = entry.body if isinstance(entry.body, dict) else {}
    if body.get("private") is True:
        classes.add("private")
    for key in _DATA_CLASS_KEYS:
        classes.update(t for t in _as_token_list(body.get(key)) if t not in {"fleet", "barb-only"})
    for raw_tag in entry.tags:
        t = raw_tag.strip().lower()
        for prefix in ("data_class:", "sensitivity:", "classification:", "level:"):
            if t.startswith(prefix) and (val := t.removeprefix(prefix).strip()):
                classes.add(val)
        if t in {"private", "sensitive", "vault_sensitive", "restricted", "secret"}:
            classes.add(t)
    for val in body.values():
        if isinstance(val, str) and "<<private:" in val.lower() and redact_memory_text(val) != val:
            classes.add("private")
    return classes


def _entry_allowed_for_caller(
    entry: MemoryEntry,
    principal: Principal | None,
    *,
    requested_repo: str | None = None,
    requested_scope: str | None = None,
    requested_data_class: str | None = None,
) -> bool:
    """Return True iff *entry* is within *principal*'s scope and data-class authorization (#3547, F07)."""
    is_admin, is_barb, caller_ids, scope_tokens, allowed_repos, allowed_dcs = _caller_access_profile(principal)
    body = entry.body if isinstance(entry.body, dict) else {}
    vis = (entry.visibility or str(body.get("visibility") or "")).strip().lower()
    if (vis == "barb-only" and not is_barb) or (vis and vis not in {"fleet", "barb-only"} and vis not in allowed_dcs):
        return False

    data_classes = _entry_data_classes(entry)
    if any(dc not in allowed_dcs for dc in data_classes):
        return False
    if requested_data_class:
        req_dc = requested_data_class.strip().lower()
        if req_dc not in allowed_dcs or req_dc not in (data_classes or {"public"}):
            return False

    target_principals = [t for k in _PRINCIPAL_KEYS for t in _as_token_list(body.get(k))]
    for raw_tag in entry.tags:
        t = raw_tag.strip().lower()
        if t.startswith(("principal:", "agent:")) and (val := t.split(":", 1)[1].strip()):
            target_principals.extend([t, val])
    if target_principals and not is_admin and not (caller_ids & set(target_principals)):
        return False

    target_scopes = [s for k in _SCOPE_KEYS for s in _as_token_list(body.get(k)) if s not in _WILDCARD_SCOPES]
    for raw_tag in entry.tags:
        t = raw_tag.strip().lower()
        if t.startswith("scope:") and (val := t.removeprefix("scope:").strip()) and val not in _WILDCARD_SCOPES:
            target_scopes.extend([t, val])
        elif t.startswith("barb:"):
            target_scopes.append(t)
    if target_scopes and not is_admin:
        barb_match = is_barb and any(s in {"barb", "barb-only"} or s.startswith("barb:") for s in target_scopes)
        if not (scope_tokens & set(target_scopes)) and not barb_match:
            return False
    if requested_scope and (req_sc := requested_scope.strip().lower()) not in _WILDCARD_SCOPES:
        sc_set = {req_sc, f"scope:{req_sc}"}
        if (not is_admin and not (sc_set & scope_tokens)) or (target_scopes and not (sc_set & set(target_scopes))):
            return False

    entry_repo_raw = entry.repo or (body.get("repo") if isinstance(body.get("repo"), str) else None)
    entry_repo = entry_repo_raw.strip().lower() if isinstance(entry_repo_raw, str) else ""
    if entry_repo and entry_repo not in _WILDCARD_SCOPES and allowed_repos is not None:
        author_match = not allowed_repos and entry.author_principal.strip().lower() in caller_ids
        if entry_repo not in allowed_repos and entry_repo not in scope_tokens and not author_match:
            return False
    if requested_repo and (req_rp := requested_repo.strip().lower()) not in _WILDCARD_SCOPES:
        if (allowed_repos and req_rp not in allowed_repos) or (
            entry_repo and entry_repo not in _WILDCARD_SCOPES and entry_repo != req_rp
        ):
            return False
    return True


def _directive_allowed_for_caller(
    directive: dict[str, Any], principal: Principal | None, *, requested_repo: str | None = None
) -> bool:
    _, is_barb, _, scope_tokens, allowed_repos, _ = _caller_access_profile(principal)
    if (directive.get("barb_only") or directive.get("is_barb_only")) and not is_barb:
        return False
    repo_val = str(directive.get("repo") or "*").strip().lower()
    if repo_val not in _WILDCARD_SCOPES:
        if allowed_repos is not None and repo_val not in allowed_repos and repo_val not in scope_tokens:
            return False
        if requested_repo and (req_rp := requested_repo.strip().lower()) not in _WILDCARD_SCOPES and repo_val != req_rp:
            return False
    return True


def _lesson_evidence_still_verifies(entry: MemoryEntry) -> bool:
    """A lesson with no locator/source_run was never evidence-bound; nothing to re-check."""
    locator = entry.body.get("locator")
    if locator is None or not entry.source_run:
        return True
    text = entry.body.get("text")
    return isinstance(text, str) and verify_lesson_evidence(
        run_stores.get_store(), entry.source_run, entry.repo or "", text, locator
    )


def get_prompt_lessons(
    store: AssistantMemoryStore | None = None,
    repo: str | None = None,
    principal: Principal | None = None,
    limit: int = 10,
) -> list[MemoryEntry]:
    """Fill up to `limit` lessons whose evidence still verifies, paging deterministically.

    A single-page fetch followed by post-hoc filtering would starve an older, still-valid
    lesson whenever enough newer lessons on that same page have since gone stale — keep
    paging past stale entries until the budget is filled or the source is exhausted.
    """
    store = store or get_memory_store()
    results: list[MemoryEntry] = []
    cursor: tuple[str, str] | None = None
    while len(results) < limit:
        page, cursor = store.list_entries_page(
            cursor=cursor,
            entry_type="lesson",
            repo=repo,
            review_status="accepted",
            include_barb_only=is_barb_principal(principal),
            active_only=True,
            limit=limit,
        )
        results.extend(entry for entry in page if _lesson_evidence_still_verifies(entry))
        if cursor is None:
            break
    return results[:limit]


def _collect_scoped_context_entries(
    store: AssistantMemoryStore,
    entry_type: str,
    limit: int,
    principal: Principal | None,
    filters: tuple[str | None, str | None, str | None] = (None, None, None),
    open_tasks_only: bool = False,
) -> list[MemoryEntry]:
    repo, scope, data_class = filters
    results: list[MemoryEntry] = []
    cursor: tuple[str, str] | None = None
    include_barb = is_barb_principal(principal)
    while len(results) < limit:
        page, cursor = store.list_entries_page(
            cursor=cursor, entry_type=entry_type, include_barb_only=include_barb, active_only=True, limit=limit * 2
        )
        for entry in page:
            if open_tasks_only and str(entry.body.get("status", "open")).lower() in _CLOSED_TASK_STATUSES:
                continue
            if _entry_allowed_for_caller(
                entry, principal, requested_repo=repo, requested_scope=scope, requested_data_class=data_class
            ):
                results.append(entry)
                if len(results) >= limit:
                    break
        if cursor is None:
            break
    return results[:limit]


def build_context_pack(
    principal: Principal | None = None,
    *,
    repo: str | None = None,
    scope: str | None = None,
    data_class: str | None = None,
) -> MemoryContextPack:
    store, flt = get_memory_store(), (repo, scope, data_class)
    digests = _collect_scoped_context_entries(store, "session_digest", CONTEXT_DIGEST_LIMIT, principal, flt)
    tasks = _collect_scoped_context_entries(store, "task", CONTEXT_TASK_LIMIT, principal, flt, open_tasks_only=True)
    directives = [
        d.to_dict()
        for d in get_directives().active()
        if _directive_allowed_for_caller(d.to_dict(), principal, requested_repo=repo)
    ]
    return MemoryContextPack(session_digests=digests, open_tasks=tasks, directives=directives, holds=current_holds())
