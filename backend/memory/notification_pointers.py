"""Filtered, deduplicated notification pointers (#3939, barb-memory slice 3).

Depends on slice 1 (context_contract.py, #3937): authority, freshness,
privacy-safety assertions, and ~400-char shared digest ceilings.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import re
from typing import Any

from memory.barb_shared_guard import (
    SHARED_DIGEST_MAX_CHARS,
    MemoryWriteProvenance,
    hold_lint_reason,
    marge_exclusion_reason,
)
from memory.context_contract import assert_entry_privacy_safe
from memory.models import MemoryEntry

DEFAULT_TRANSCRIPT_RETENTION_SECONDS = 3 * 24 * 3600  # 3 days
_WHITESPACE_RE = re.compile(r"\s+")
_SEVERITY_RANK = {"critical": 3, "error": 2, "warning": 1, "info": 0}


def cap_digest_summary(text: str, max_chars: int = SHARED_DIGEST_MAX_CHARS) -> str:
    cleaned = text.strip()
    return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 3].rstrip() + "..."


def notification_dedup_key(source_product: str, topic: str, target: str | None, summary: str) -> str:
    sp = source_product.strip().lower()
    top = topic.strip().lower()
    tgt = (target or "").strip().lower()
    norm_summary = _WHITESPACE_RE.sub(" ", summary.strip().lower())
    digest = hashlib.sha256(f"{sp}|{top}|{tgt}|{norm_summary}".encode()).hexdigest()[:24]
    return f"npk_{digest}"


@dataclass(frozen=True)
class NotificationPointer:
    pointer_id: str
    notification_id: str
    source_product: str
    topic: str
    summary: str
    created_at: str
    author_principal: str
    severity: str = "info"
    tags: tuple[str, ...] = ()
    ref: str | None = None
    target: str | None = None
    occurrence_count: int = 1


@dataclass
class RawNotification:
    notification_id: str
    source_product: str
    topic: str
    summary: str
    author_principal: str = ""
    severity: str = "info"
    tags: tuple[str, ...] = ()
    target: str | None = None
    ref: str | None = None
    created_at: str = ""
    body: dict[str, Any] = field(default_factory=dict)


def _to_raw(notif: RawNotification | Mapping[str, Any]) -> RawNotification:
    if isinstance(notif, RawNotification):
        return notif
    tags_raw = notif.get("tags", ())
    tags_tuple = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, (list, tuple, set)) else ()
    body_raw = notif.get("body", {})
    body_dict = dict(body_raw) if isinstance(body_raw, Mapping) else {}
    return RawNotification(
        notification_id=str(notif.get("notification_id", "")),
        source_product=str(notif.get("source_product", "")),
        topic=str(notif.get("topic", "")),
        summary=str(notif.get("summary", "")),
        author_principal=str(notif.get("author_principal", "")),
        severity=str(notif.get("severity", "info")),
        tags=tags_tuple,
        target=str(notif.get("target")) if notif.get("target") is not None else None,
        ref=str(notif.get("ref")) if notif.get("ref") is not None else None,
        created_at=str(notif.get("created_at", "")),
        body=body_dict,
    )


def filter_notification(notification: RawNotification | Mapping[str, Any]) -> NotificationPointer | None:
    raw = _to_raw(notification)
    tags_tuple = tuple(raw.tags) if raw.tags else ()
    prov = MemoryWriteProvenance(
        source_product=raw.source_product,
        tags=tags_tuple,
        body=dict(raw.body, summary=raw.summary, topic=raw.topic),
        author_principal=raw.author_principal,
    )
    if marge_exclusion_reason(prov) is not None or hold_lint_reason(raw.summary, raw.topic) is not None:
        return None
    capped = cap_digest_summary(raw.summary)
    key = notification_dedup_key(raw.source_product, raw.topic, raw.target, capped)
    pointer_id = f"np_{raw.notification_id}" if raw.notification_id else f"np_{key}"
    return NotificationPointer(
        pointer_id=pointer_id,
        notification_id=raw.notification_id,
        source_product=raw.source_product,
        topic=raw.topic,
        summary=capped,
        created_at=raw.created_at,
        author_principal=raw.author_principal,
        severity=raw.severity,
        tags=tags_tuple,
        ref=raw.ref,
        target=raw.target,
        occurrence_count=1,
    )


def deduplicate_notification_pointers(
    incoming: Sequence[NotificationPointer],
    existing: Sequence[NotificationPointer] | None = None,
) -> tuple[NotificationPointer, ...]:
    groups: OrderedDict[str, list[NotificationPointer]] = OrderedDict()
    for ptr in list(existing or ()) + list(incoming):
        key = notification_dedup_key(ptr.source_product, ptr.topic, ptr.target, ptr.summary)
        groups.setdefault(key, []).append(ptr)

    collapsed: list[NotificationPointer] = []
    for members in groups.values():
        if len(members) == 1:
            collapsed.append(members[0])
            continue
        winner = max(members, key=lambda m: (_SEVERITY_RANK.get(m.severity.lower(), 0), m.created_at))
        total_occurrences = sum(m.occurrence_count for m in members)
        collapsed.append(replace(winner, occurrence_count=total_occurrences))
    return tuple(collapsed)


def refresh_notification_pointers(
    notifications: Sequence[RawNotification | Mapping[str, Any]],
    existing: Sequence[NotificationPointer] | None = None,
) -> tuple[NotificationPointer, ...]:
    filtered = [ptr for notif in notifications if (ptr := filter_notification(notif)) is not None]
    return deduplicate_notification_pointers(filtered, existing=existing)


def pointer_to_memory_entry(pointer: NotificationPointer, *, visibility: str = "fleet") -> MemoryEntry:
    entry = MemoryEntry(
        id=pointer.pointer_id,
        entry_type="session_digest",
        author_principal=pointer.author_principal or "system:notification-pointer",
        source_product=pointer.source_product or "dashboard",
        tags=list(pointer.tags),
        body={
            "summary": pointer.summary,
            "topic": pointer.topic,
            "notification_id": pointer.notification_id,
            "ref": pointer.ref,
            "occurrence_count": pointer.occurrence_count,
        },
        created_at=pointer.created_at,
        visibility=visibility,
    )
    assert_entry_privacy_safe(entry)
    return entry


@dataclass
class RawTranscript:
    transcript_id: str
    raw_text: str
    source_product: str
    author_principal: str
    tags: tuple[str, ...]
    created_at: str
    used: bool = False


def _is_valid_iso(val: str) -> bool:
    try:
        datetime.fromisoformat(val)
        return True
    except (TypeError, ValueError):
        return False


class LocalTranscriptBuffer:
    def __init__(self, retention_seconds: float = DEFAULT_TRANSCRIPT_RETENTION_SECONDS) -> None:
        self._retention_seconds = retention_seconds
        self._transcripts: dict[str, RawTranscript] = {}

    def __len__(self) -> int:
        return len(self._transcripts)

    def append(
        self,
        transcript_id: str,
        raw_text: str,
        source_product: str = "dashboard",
        author_principal: str = "",
        tags: Sequence[str] = (),
        created_at: str = "",
    ) -> bool:
        tags_tuple = tuple(tags)
        prov = MemoryWriteProvenance(
            source_product=source_product,
            tags=tags_tuple,
            body={"raw_text": raw_text},
            author_principal=author_principal,
        )
        if marge_exclusion_reason(prov) is not None:
            return False
        self._transcripts[transcript_id] = RawTranscript(
            transcript_id=transcript_id,
            raw_text=raw_text,
            source_product=source_product,
            author_principal=author_principal,
            tags=tags_tuple,
            created_at=created_at,
            used=False,
        )
        return True

    def get(self, transcript_id: str) -> RawTranscript | None:
        item = self._transcripts.get(transcript_id)
        if item is not None:
            item.used = True
        return item

    def prune(self, *, now_iso: str) -> int:
        try:
            now_dt = datetime.fromisoformat(now_iso)
        except (TypeError, ValueError):
            return 0
        to_delete = [
            tid
            for tid, item in self._transcripts.items()
            if (
                (now_dt - datetime.fromisoformat(item.created_at)).total_seconds() > self._retention_seconds
                if _is_valid_iso(item.created_at)
                else True
            )
        ]
        for tid in to_delete:
            del self._transcripts[tid]
        return len(to_delete)

