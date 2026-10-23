"""Agent escalation and Barb inbox integration (#2237)."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from staff.conversation_models import ThreadRecord
from staff.conversations import ConversationStore
from staff.redaction import redact_sensitive_content

log = logging.getLogger("dashboard.staff.concerns")
CONCERNS_THREAD_ID = "thread_barb_agent_concerns"
DEFAULT_RATE_LIMIT_PER_DAY = 10
SUMMARY_MAX_CHARS, DETAIL_MAX_CHARS = 500, 4000

ConcernKind = Literal["concern", "question", "blocker"]
ConcernSeverity = Literal["low", "medium", "high", "critical"]
ConcernStatus = Literal["open", "acknowledged", "resolved", "declined"]


class RateLimitError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limit exceeded: retry after {retry_after} seconds")
        self.retry_after = retry_after


class SecretBearingError(ValueError):
    """Raised when concern content contains sensitive secrets."""


@dataclass(frozen=True)
class ConcernRecord:
    id: str
    kind: ConcernKind
    severity: ConcernSeverity
    repo: str
    summary: str
    detail: str
    ask: str
    author_principal: str
    status: ConcernStatus
    count: int = 1
    issue: int | None = None
    pr: int | None = None
    run_id: str | None = None
    session_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    untrusted: bool = True
    created_at: str = ""
    updated_at: str = ""
    reply: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    proposal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_meta(cls, m: dict[str, Any]) -> ConcernRecord:
        d = dict(m)
        d["id"] = d.pop("concern_id", d.get("id", ""))
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in fields(cls)}})


def _get_or_create_thread(store: ConversationStore) -> ThreadRecord:
    th = store.get_thread(CONCERNS_THREAD_ID)
    if th is not None:
        return th
    return store.create_thread(
        title="Barb Agent Concerns",
        kind="auto",
        participants=["barb"],
        created_by="system",
        thread_id=CONCERNS_THREAD_ID,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def create_or_dedup_concern(
    store: ConversationStore,
    *,
    author: str,
    kind: ConcernKind,
    repo: str,
    summary: str,
    detail: str,
    severity: ConcernSeverity = "medium",
    ask: str = "",
    issue: int | None = None,
    pr: int | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    evidence_refs: list[str] | None = None,
    idempotency_key: str | None = None,
) -> tuple[ConcernRecord, bool]:
    _get_or_create_thread(store)
    if any(t and redact_sensitive_content(t) != t for t in (summary, detail, ask)):
        raise SecretBearingError("Secret-bearing concerns are rejected")

    norm_sum, now_dt, now_str = summary.strip().lower(), datetime.now(UTC), _now_iso()
    cutoff = (now_dt - timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    with store._lock:
        rows = store._conn.execute(
            "SELECT id, meta, created_at FROM messages WHERE thread_id = ? AND created_at >= ? ORDER BY seq ASC",
            (CONCERNS_THREAD_ID, cutoff),
        ).fetchall()
        p_concerns = [
            (r["id"], json.loads(r["meta"]), r["created_at"])
            for r in rows
            if r["meta"] and json.loads(r["meta"]).get("author_principal") == author
        ]
        for row_id, meta, _ in p_concerns:
            if meta.get("repo") == repo and meta.get("issue") == issue and meta.get("norm_sum") == norm_sum:
                meta["count"] = int(meta.get("count", 1)) + 1
                meta["updated_at"] = now_str
                store._conn.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta), row_id))
                return ConcernRecord.from_meta(meta), False

        if len(p_concerns) >= DEFAULT_RATE_LIMIT_PER_DAY:
            try:
                oldest = datetime.fromisoformat(p_concerns[0][2].replace("Z", "+00:00"))
                retry = max(1, int((oldest + timedelta(hours=24) - now_dt).total_seconds()))
            except Exception:
                retry = 3600
            raise RateLimitError(retry)

        cid = f"concern_{uuid.uuid4().hex[:12]}"
        meta_dict = {
            "concern_id": cid,
            "kind": kind,
            "severity": severity,
            "repo": repo,
            "issue": issue,
            "pr": pr,
            "run_id": run_id,
            "session_id": session_id,
            "summary": summary,
            "detail": detail,
            "ask": ask,
            "author_principal": author,
            "status": "open",
            "count": 1,
            "evidence_refs": evidence_refs or [],
            "untrusted": True,
            "created_at": now_str,
            "updated_at": now_str,
            "norm_sum": norm_sum,
        }
        body_md = (
            f"### Untrusted Concern: {summary}\n\n**Author:** `{author}` | **Repo:** `{repo}`\n\n"
            f"> {ask}\n\n```{detail}```"
        )
        store.add_message(
            thread_id=CONCERNS_THREAD_ID,
            author_kind="user",
            author=author,
            kind="text",
            body_md=body_md,
            meta=meta_dict,
            run_id=run_id or "",
            idempotency_key=idempotency_key,
        )
        return ConcernRecord.from_meta(meta_dict), True


def list_concerns(
    store: ConversationStore, *, status: str | None = None, repo: str | None = None, limit: int = 50
) -> list[ConcernRecord]:
    _get_or_create_thread(store)
    with store._lock:
        rows = store._conn.execute(
            "SELECT meta FROM messages WHERE thread_id = ? ORDER BY seq DESC", (CONCERNS_THREAD_ID,)
        ).fetchall()
    out: list[ConcernRecord] = []
    for r in rows:
        m = json.loads(r["meta"]) if r["meta"] else {}
        if m.get("concern_id") and (not status or m.get("status") == status) and (not repo or m.get("repo") == repo):
            out.append(ConcernRecord.from_meta(m))
            if len(out) >= limit:
                break
    return out


def get_concern(store: ConversationStore, concern_id: str) -> ConcernRecord | None:
    return next((c for c in list_concerns(store, limit=500) if c.id == concern_id), None)


def resolve_concern(
    store: ConversationStore,
    concern_id: str,
    *,
    status: ConcernStatus,
    reply: str,
    resolved_by: str,
    proposal: dict[str, Any] | None = None,
) -> ConcernRecord:
    assert status in ("resolved", "declined"), f"Invalid resolution status: {status}"
    _get_or_create_thread(store)
    with store._lock:
        rows = store._conn.execute(
            "SELECT id, meta FROM messages WHERE thread_id = ?", (CONCERNS_THREAD_ID,)
        ).fetchall()
        target = next(
            (r for r in rows if (json.loads(r["meta"]) if r["meta"] else {}).get("concern_id") == concern_id), None
        )
        if target is None:
            raise KeyError(f"Concern {concern_id} not found")
        meta, mid = json.loads(target["meta"]), target["id"]

    pid: str | None = None
    if proposal:
        p = dict(proposal.get("params") or {})
        if "rationale" in proposal and "rationale" not in p:
            p["rationale"] = proposal["rationale"]
        prop = store.create_proposal(
            message_id=mid,
            thread_id=CONCERNS_THREAD_ID,
            action=str(proposal.get("action") or "staff.dispatch"),
            params=p,
            risk=str(proposal.get("risk") or "medium"),
            principal=resolved_by,
        )
        pid = prop.id

    with store._lock:
        meta.update(
            {
                "status": status,
                "reply": reply,
                "resolved_by": resolved_by,
                "resolved_at": _now_iso(),
                "updated_at": _now_iso(),
                "proposal_id": pid,
            }
        )
        store._conn.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta), mid))
        return ConcernRecord.from_meta(meta)
