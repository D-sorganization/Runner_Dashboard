"""Agent escalation and concern handling into Barb's inbox (#2237)."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from staff.conversations import ConversationStore
from staff.inbox_models import InboxItem
from staff.redaction import redact_sensitive_content

log = logging.getLogger("dashboard.staff.concerns")
BARB_CONCERNS_THREAD_ID = "th_barb_concerns"
CONCERNS_RATE_LIMIT_PER_DAY = int(os.environ.get("CONCERNS_RATE_LIMIT_PER_DAY", "10"))

_MEMORY_LOCK = threading.Lock()
_AUTHOR_POSTS: dict[str, list[datetime]] = {}


class SecretDetectedError(ValueError):
    """Raised when secrets or credentials are found in submitted concern text."""


class RateLimitExceededError(Exception):
    """Raised when an author exceeds the 24-hour rate limit."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")
        self.retry_after = retry_after


def reset_concerns_memory() -> None:
    with _MEMORY_LOCK:
        _AUTHOR_POSTS.clear()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _ensure_concerns_thread(c_store: ConversationStore) -> None:
    if c_store.get_thread(BARB_CONCERNS_THREAD_ID) is None:
        try:
            c_store.create_thread(
                title="Barb Agent Escalations & Concerns",
                kind="panel",
                participants=["barb-concerns"],
                created_by="system",
                thread_id=BARB_CONCERNS_THREAD_ID,
                meta={"system": True, "purpose": "agent_concerns"},
            )
        except Exception:  # noqa: BLE001
            pass


def _check_secrets(summary: str, detail: str) -> None:
    if redact_sensitive_content(summary) != summary or redact_sensitive_content(detail) != detail:
        raise SecretDetectedError("Secret detected in concern; secrets must be redacted before submission.")


def _check_rate_limit(author: str) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    with _MEMORY_LOCK:
        history = [ts for ts in _AUTHOR_POSTS.get(author, []) if ts > cutoff]
        if len(history) >= CONCERNS_RATE_LIMIT_PER_DAY:
            oldest = min(history)
            retry_after = max(1, int((oldest + timedelta(hours=24) - now).total_seconds()))
            raise RateLimitExceededError(retry_after=retry_after)
        history.append(now)
        _AUTHOR_POSTS[author] = history


def format_untrusted_markdown(c: dict[str, Any]) -> str:
    """Format concern as explicitly quoted untrusted input for Barb's context."""
    author, kind, sev = c.get("author", "unknown"), c.get("kind", "concern"), c.get("severity", "medium")
    repo, issue, run_id = c.get("repo", "-"), c.get("issue") or "-", c.get("run_id") or "-"
    return (
        f"> [!CAUTION] UNTRUSTED AGENT INPUT\n"
        f"> **Author:** {author} | **Kind:** {kind} | **Severity:** {sev}\n"
        f"> **Repo:** {repo} | **Issue:** {issue} | **Run:** {run_id}\n"
        f"> **Summary:** {c.get('summary', '')}\n\n"
        f"**Detail:**\n{c.get('detail', '')}\n\n"
        f"**Ask:**\n{c.get('ask', '')}\n"
    )


def submit_concern(
    c_store: ConversationStore,
    *,
    author: str,
    idempotency_key: str,
    data: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Submit an agent concern. Returns (concern_dict, is_duplicate)."""
    summary, detail = data.get("summary", ""), data.get("detail", "")
    _check_secrets(summary, detail)
    _ensure_concerns_thread(c_store)
    issue = data.get("issue")
    repo = data.get("repo", "")
    norm_summary = summary.strip().lower()
    dedup_key = f"{author}:{repo}:{issue or 0}:{norm_summary}"
    cutoff_iso = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    with c_store._lock:
        rows = c_store._conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND created_at >= ? ORDER BY seq DESC",
            (BARB_CONCERNS_THREAD_ID, cutoff_iso),
        ).fetchall()
        for row in rows:
            meta = json.loads(row["meta"]) if row["meta"] else {}
            if meta.get("dedup_key") == dedup_key:
                meta["duplicate_count"] = int(meta.get("duplicate_count", 1)) + 1
                meta["updated_at"] = _now()
                c_store._conn.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta), row["id"]))
                return meta, True

    _check_rate_limit(author)
    now_iso = _now()
    concern: dict[str, Any] = {
        "id": f"concern_{uuid.uuid4().hex[:12]}",
        "author": author,
        "kind": data.get("kind", "concern"),
        "severity": data.get("severity", "medium"),
        "repo": repo,
        "issue": issue,
        "pr": data.get("pr"),
        "run_id": data.get("run_id") or os.environ.get("STAFF_RUN_ID", ""),
        "session_id": data.get("session_id") or "",
        "summary": summary[:500],
        "detail": detail[:4000],
        "evidence_refs": (data.get("evidence_refs") or [])[:10],
        "ask": data.get("ask", ""),
        "status": "open",
        "resolution": None,
        "duplicate_count": 1,
        "created_at": now_iso,
        "updated_at": now_iso,
        "untrusted": True,
        "dedup_key": dedup_key,
    }
    c_store.add_message(
        thread_id=BARB_CONCERNS_THREAD_ID,
        author_kind="role",
        author=author,
        kind="text",
        body_md=format_untrusted_markdown(concern),
        meta=concern,
        run_id=concern["run_id"],
        idempotency_key=idempotency_key,
    )
    return concern, False


def _iter_concerns(c_store: ConversationStore, limit: int = 100) -> list[dict[str, Any]]:
    _ensure_concerns_thread(c_store)
    with c_store._lock:
        rows = c_store._conn.execute(
            "SELECT id, meta FROM messages WHERE thread_id = ? ORDER BY seq DESC LIMIT ?",
            (BARB_CONCERNS_THREAD_ID, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = json.loads(r["meta"]) if r["meta"] else {}
        if meta.get("id", "").startswith("concern_"):
            meta["_msg_id"] = r["id"]
            out.append(meta)
    return out


def list_concerns(
    c_store: ConversationStore, *, status: str | None = None, repo: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    items = [
        c
        for c in _iter_concerns(c_store, limit * 2)
        if (not status or c.get("status") == status) and (not repo or c.get("repo") == repo)
    ]
    return [{k: v for k, v in c.items() if k != "_msg_id"} for c in items[:limit]]


def get_concern(c_store: ConversationStore, concern_id: str) -> dict[str, Any] | None:
    c = next((c for c in _iter_concerns(c_store, 200) if c.get("id") == concern_id), None)
    return {k: v for k, v in c.items() if k != "_msg_id"} if c else None


def resolve_concern(
    c_store: ConversationStore,
    concern_id: str,
    *,
    status: str = "resolved",
    reply: str,
    action: str | None = None,
    action_params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    c = next((c for c in _iter_concerns(c_store, 200) if c.get("id") == concern_id), None)
    if not c:
        return None
    msg_id = c.pop("_msg_id")
    c["status"] = status
    c["updated_at"] = _now()
    prop_id = None
    if action in {"staff.dispatch", "board.propose"}:
        prop = c_store.create_proposal(
            message_id=msg_id,
            thread_id=BARB_CONCERNS_THREAD_ID,
            action=action,
            params=action_params or {},
            principal="barb",
            risk="medium",
        )
        prop_id = prop.id
    c["resolution"] = {"reply": reply, "resolved_at": _now(), "proposal_id": prop_id}
    with c_store._lock:
        c_store._conn.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(c), msg_id))
    return c


def collect_agent_concerns(c_store: ConversationStore) -> list[InboxItem]:
    """Collect open agent concerns for Barb's inbox."""
    concerns = list_concerns(c_store, status="open", limit=50)
    items: list[InboxItem] = []
    for c in concerns:
        sev = c.get("severity", "medium")
        if sev not in {"low", "medium", "high", "critical"}:
            sev = "medium"
        items.append(
            InboxItem(
                id=f"concern_{c['id']}",
                source="agent_concern",
                title=f"Agent concern: {c.get('summary', '')}",
                summary=c.get("ask") or c.get("detail") or "",
                severity=sev,  # type: ignore[arg-type]
                created_at=c.get("created_at", _now()),
                link=f"/staff?thread={BARB_CONCERNS_THREAD_ID}&concern={c['id']}",
                metadata=c,
            )
        )
    return items
