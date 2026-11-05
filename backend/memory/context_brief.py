"""Rebuildable context brief (#3940, barb-memory slice 4).

Capped at ~2,000 tokens, redaction re-run on every rebuild, advisory-only
until 5 passing runs. Depends on slice 1 (context_contract.py, #3937) and
slice 2 (context_freshness_guard.py, #3938).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from memory.barb_shared_guard import SHARED_DIGEST_MAX_CHARS, entry_allowed_in_shared_memory
from memory.context_freshness_guard import guard_context
from memory.models import MemoryEntry
from memory.redact import redact_memory_text

DEFAULT_MAX_BRIEF_TOKENS = 2000
CHARS_PER_TOKEN = 4
MIN_PASSING_RUNS = 5


def estimate_tokens(text: str) -> int:
    """Estimate token count at ~4 chars per token for context budget enforcement."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


@dataclass
class BriefRunTracker:
    """Tracks rebuild dry-run passes; context brief remains advisory until 5 passes."""

    passing_runs: int = 0
    total_runs: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_advisory(self) -> bool:
        return self.passing_runs < MIN_PASSING_RUNS

    def record_pass(
        self,
        *,
        run_id: str | None = None,
        timestamp: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        self.passing_runs += 1
        self.total_runs += 1
        record: dict[str, Any] = {
            "success": True,
            "pass_number": self.passing_runs,
            "total_runs": self.total_runs,
            "run_id": run_id,
            "timestamp": timestamp,
        }
        if details:
            record["details"] = dict(details)
        self.history.append(record)
        return self.passing_runs

    def record_failure(
        self,
        *,
        run_id: str | None = None,
        timestamp: str | None = None,
        reason: str = "",
    ) -> int:
        self.total_runs += 1
        self.history.append(
            {
                "success": False,
                "total_runs": self.total_runs,
                "run_id": run_id,
                "timestamp": timestamp,
                "reason": reason,
            }
        )
        return self.passing_runs

    def reset(self) -> None:
        self.passing_runs = 0
        self.total_runs = 0
        self.history.clear()


@dataclass(frozen=True)
class ContextBrief:
    """The generated Barb shared context brief."""

    content: str
    token_count: int
    is_advisory: bool
    passing_runs: int
    entry_ids: tuple[str, ...]
    built_at: str
    sections: tuple[str, ...]
    fresh_count: int
    stale_count: int
    suppressed_count: int


def _cap_summary(text: str, max_chars: int = SHARED_DIGEST_MAX_CHARS) -> str:
    cleaned = text.strip()
    return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 3].rstrip() + "..."


def _entry_summary(entry: MemoryEntry) -> str:
    raw = str(entry.body.get("summary") or entry.body.get("topic") or entry.body.get("rule") or "").strip()
    return _cap_summary(raw)


def build_context_brief(
    entries: Sequence[MemoryEntry],
    *,
    now_iso: str,
    tracker: BriefRunTracker | None = None,
    max_tokens: int = DEFAULT_MAX_BRIEF_TOKENS,
    include_stale: bool = True,
) -> ContextBrief:
    """Build a rebuildable context brief capped at *max_tokens*.

    - Resolves conflicts and classifies freshness via :func:`guard_context`.
    - Filters out non-privacy-safe entries.
    - Prioritizes higher-authority entry types (decisions > lessons > tasks > digests).
    - Enforces the token budget strictly.
    - Re-runs secret redaction on every build.
    - Stays advisory-only until 5 passing runs are recorded in *tracker*.
    """
    passing_runs = tracker.passing_runs if tracker is not None else 0
    is_advisory = tracker.is_advisory if tracker is not None else True

    guarded = guard_context(list(entries), now_iso=now_iso)
    suppressed_count = sum(len(g.suppressed) for g in guarded.conflicts)

    # Privacy safety filter
    candidate_entries = [e for e in guarded.fresh if entry_allowed_in_shared_memory(e)]
    if include_stale:
        candidate_entries.extend(e for e in guarded.stale if entry_allowed_in_shared_memory(e))

    # Bucket by priority category
    decisions = [e for e in candidate_entries if e.entry_type == "decision"]
    lessons = [e for e in candidate_entries if e.entry_type == "lesson"]
    tasks = [e for e in candidate_entries if e.entry_type == "task"]
    digests = [e for e in candidate_entries if e.entry_type not in ("decision", "lesson", "task")]

    for bucket in (decisions, lessons, tasks, digests):
        bucket.sort(key=lambda e: e.created_at, reverse=True)

    status_str = f"ADVISORY (pass {passing_runs}/{MIN_PASSING_RUNS})" if is_advisory else "ACTIVE"
    header_lines = [
        "# Barb Shared Context Brief",
        f"Built: {now_iso} | Status: {status_str}",
        "",
    ]
    base_header = "\n".join(header_lines)

    selected_ids: list[str] = []
    sections_present: list[str] = []
    content_blocks: list[str] = [base_header]

    categories: tuple[tuple[str, list[MemoryEntry]], ...] = (
        ("Decisions", decisions),
        ("Lessons", lessons),
        ("Active Tasks", tasks),
        ("Session Digests & Context", digests),
    )

    current_text = "\n".join(content_blocks)
    max_chars = max_tokens * CHARS_PER_TOKEN

    for section_title, bucket in categories:
        if not bucket:
            continue
        section_lines = [f"## {section_title}"]
        items_added = 0
        for entry in bucket:
            summary = _entry_summary(entry)
            repo_label = entry.repo or "global"
            author_label = entry.author_principal or "unknown"
            line = f"- [{entry.id}] ({repo_label} | {author_label}): {summary}"

            candidate_text = current_text + "\n" + "\n".join(section_lines + [line]) + "\n"
            if estimate_tokens(candidate_text) > max_tokens:
                break

            section_lines.append(line)
            selected_ids.append(entry.id)
            items_added += 1

        if items_added > 0:
            section_block = "\n".join(section_lines) + "\n"
            content_blocks.append(section_block)
            sections_present.append(section_title)
            current_text = "\n".join(content_blocks)

    raw_content = "\n".join(content_blocks).rstrip() + "\n"

    # Strict token cap clamp if needed
    if estimate_tokens(raw_content) > max_tokens:
        raw_content = raw_content[:max_chars].rstrip() + "\n"

    # Redaction re-run on every rebuild
    redacted_content = redact_memory_text(raw_content)
    token_count = estimate_tokens(redacted_content)

    return ContextBrief(
        content=redacted_content,
        token_count=token_count,
        is_advisory=is_advisory,
        passing_runs=passing_runs,
        entry_ids=tuple(selected_ids),
        built_at=now_iso,
        sections=tuple(sections_present),
        fresh_count=len(guarded.fresh),
        stale_count=len(guarded.stale),
        suppressed_count=suppressed_count,
    )
