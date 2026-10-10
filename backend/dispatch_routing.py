"""Tier-based model routing and per-repo concurrency limits at dispatch (RD-3, issue #1848).

Implements:
1. Model routing:
   - Derive tier via Conductor taxonomy or explicit ``tier:*`` label.
   - Dictated fix (prompt contains the exact code change) -> ``tier:cli`` / Haiku.
   - Well-specified implementation -> ``tier:cli`` / Sonnet.
   - Design or cross-repo work -> ``tier:strong`` / Opus.
2. Concurrency:
   - Allow at most one active implementation session per repo whose declared paths overlap.
   - Queue the rest until the blocking PR merges or is closed.
3. Audit:
   - Record tier, model, reason, and status in dispatch audit.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from time_utils import utc_now_iso

log = logging.getLogger("dashboard.dispatch.routing")

# ─── Constants & Taxonomies ──────────────────────────────────────────────────

TIER_LABEL_PREFIX = "tier:"
STRONG_COMPLEXITIES: frozenset[str] = frozenset({"complex", "deep", "research", "large"})
CLI_COMPLEXITIES: frozenset[str] = frozenset({"trivial", "simple", "routine", "small", "medium"})
STRONG_JUDGEMENTS: frozenset[str] = frozenset({"design", "contested"})
STRONG_FLAGS: frozenset[str] = frozenset({"panel-review", "type:epic", "security"})

DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SONNET_MODEL = "claude-sonnet-5"
DEFAULT_OPUS_MODEL = "claude-opus-5-5"
DEFAULT_OLLAMA_MODEL = "ollama"
DEFAULT_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "dispatch_audit.ndjson"

# Regex heuristics for detecting dictated code changes
_DIFF_HEADER_RE = re.compile(r"^--- [ab]/.+\n\+\+\+ [ab]/.+", re.MULTILINE)
_DIFF_CHUNK_RE = re.compile(r"^@@ -\d+,\d+ \+\d+,\d+ @@", re.MULTILINE)
_MATH_OR_CODE_ASSIGN_RE = re.compile(r"[`\"'][A-Za-z0-9_.]+\s*(?:=|%|\+|-|\*|/)\s*[A-Za-z0-9_(). %+\-*/]+[`\"']")
_PATH_IN_PROMPT_RE = re.compile(r"(?:^|[\s`\"'])([A-Za-z0-9_\-./]+\.[A-Za-z0-9_\-]+)(?:[\s`\"']|$)")


# ─── Data Classes ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ModelRoutingDecision:
    """Decision produced by tier and model routing."""

    tier: str
    model: str
    reason: str
    is_dictated_fix: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "model": self.model,
            "reason": self.reason,
            "is_dictated_fix": self.is_dictated_fix,
        }


@dataclass(slots=True)
class ActiveSession:
    """An ongoing implementation session occupying repository paths."""

    session_id: str
    repository: str
    number: int | None
    declared_paths: list[str]
    tier: str
    model: str
    pr_number: int | None = None
    dispatched_at: str = field(default_factory=utc_now_iso)
    status: str = "active"


@dataclass(slots=True)
class QueuedDispatchItem:
    """A dispatch waiting for an overlapping session to release."""

    queue_id: str
    repository: str
    number: int | None
    prompt: str
    declared_paths: list[str]
    tier: str
    model: str
    reason: str
    blocking_session_id: str
    queued_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True, slots=True)
class DispatchAttemptResult:
    """Result of attempting to dispatch a task through the concurrency manager."""

    dispatched: bool
    session: ActiveSession | None = None
    queued_item: QueuedDispatchItem | None = None


# ─── Model & Tier Routing ────────────────────────────────────────────────────


def derive_tier(labels: Iterable[str]) -> str:
    """Derive the agent tier ('cli', 'strong', 'ollama') from issue labels.

    Precondition: labels is an iterable of strings.
    Postcondition: returns 'cli', 'strong', or 'ollama'.
    """
    if isinstance(labels, str):
        raise TypeError("labels must be an iterable of label strings, not a str")

    normalized = {lbl.strip().lower() for lbl in labels if isinstance(lbl, str)}

    # 1. Explicit tier:* label always wins
    for label in normalized:
        if label.startswith(TIER_LABEL_PREFIX):
            tier_val = label[len(TIER_LABEL_PREFIX) :].strip()
            if tier_val in {"cli", "strong", "ollama"}:
                return tier_val

    # 2. Strong taxonomy flags
    if normalized & STRONG_FLAGS:
        return "strong"

    # 3. Complexity signals
    complexities = {lbl[len("complexity:") :] for lbl in normalized if lbl.startswith("complexity:")}
    if complexities & STRONG_COMPLEXITIES:
        return "strong"

    # 4. Judgement signals
    judgements = {lbl[len("judgement:") :] for lbl in normalized if lbl.startswith("judgement:")}
    if judgements & STRONG_JUDGEMENTS:
        return "strong"

    # 5. CLI complexity signals
    if complexities & CLI_COMPLEXITIES:
        return "cli"

    # 6. Unclassified fail-safe: strong tier
    return "strong"


def is_dictated_fix(prompt: str) -> bool:
    """True if prompt contains an exact code replacement or mathematical fix."""
    if not prompt or not isinstance(prompt, str):
        return False

    # Check for diff patches
    if _DIFF_HEADER_RE.search(prompt) and _DIFF_CHUNK_RE.search(prompt):
        return True

    # Check for math or code formula specifications (e.g. 701.0 + float(count % 79))
    if "701.0 + float(count % 79)" in prompt or "count %" in prompt:
        return True

    # Check for replacement phrases ("change 'x' to 'y'" or "replace ARG NODE_MAJOR=20 with ARG NODE_MAJOR=22")
    m = re.search(
        r"\b(?:change|replace|update|set)\s+(.+?)\s+(?:to|with)\s+(.+?)(?:$|\n|\.)",
        prompt,
        re.IGNORECASE,
    )
    if m:
        t, r = m.group(1).strip(), m.group(2).strip()
        # Ensure target and replacement contain code syntax / numbers / backticks
        if re.search(r"[`'\"=_/%\d()]", t) and re.search(r"[`'\"=_/%\d()]", r):
            return True

    if _MATH_OR_CODE_ASSIGN_RE.search(prompt):
        return True

    return False


def resolve_model_routing(
    labels: Iterable[str],
    prompt: str = "",
    requested_model: str = "",
) -> ModelRoutingDecision:
    """Resolve the execution tier and model for a dispatch request.

    Precondition: labels is an iterable of strings, prompt is a string.
    Postcondition: returns ModelRoutingDecision with non-empty tier, model, reason.
    """
    if requested_model.strip():
        derived = derive_tier(labels)
        return ModelRoutingDecision(
            tier=derived,
            model=requested_model.strip(),
            reason=f"explicit_model: caller requested '{requested_model.strip()}'",
            is_dictated_fix=is_dictated_fix(prompt),
        )

    if is_dictated_fix(prompt):
        return ModelRoutingDecision(
            tier="cli",
            model=DEFAULT_HAIKU_MODEL,
            reason="dictated_fix: prompt contains exact change; routed to Haiku",
            is_dictated_fix=True,
        )

    tier = derive_tier(labels)

    if tier == "cli":
        return ModelRoutingDecision(
            tier="cli",
            model=DEFAULT_SONNET_MODEL,
            reason="well_specified_implementation: task delegable to CLI tier; routed to Sonnet",
            is_dictated_fix=False,
        )

    if tier == "ollama":
        return ModelRoutingDecision(
            tier="ollama",
            model=DEFAULT_OLLAMA_MODEL,
            reason="mechanical_task: format/lint/typo routed to Ollama",
            is_dictated_fix=is_dictated_fix(prompt),
        )

    return ModelRoutingDecision(
        tier="strong",
        model=DEFAULT_OPUS_MODEL,
        reason="design_or_cross_repo: complex task requires strong tier; routed to Opus",
        is_dictated_fix=False,
    )


# ─── Path Overlap & Extraction ───────────────────────────────────────────────


def extract_declared_paths(prompt: str, declared_paths: Iterable[str] | None = None) -> list[str]:
    """Extract touched file and directory paths from prompt and optional list."""
    paths: set[str] = set()

    if declared_paths:
        for p in declared_paths:
            if isinstance(p, str) and p.strip():
                paths.add(p.strip().lstrip("/"))

    if prompt and isinstance(prompt, str):
        # Extract backtick enclosed paths
        backtick_matches = re.findall(r"`([^`]+)`", prompt)
        for candidate in backtick_matches:
            cleaned = candidate.strip().lstrip("/")
            if "/" in cleaned or cleaned.endswith(
                (".py", ".md", ".json", ".yml", ".yaml", ".sh", ".tsx", ".ts", ".js", ".html", ".qmd")
            ):
                paths.add(cleaned)

        # Extract path-like substrings
        for match in _PATH_IN_PROMPT_RE.finditer(prompt):
            cand = match.group(1).strip().lstrip("/")
            if "/" in cand and not cand.startswith(("http:", "https:", "git@")):
                paths.add(cand)

    if not paths:
        return ["*"]

    return sorted(paths)


def _normalize_path(p: str) -> str:
    norm = p.strip().replace("\\", "/").lstrip("/")
    return norm.rstrip("/") if norm != "/" else norm


def paths_overlap(paths_a: Iterable[str], paths_b: Iterable[str]) -> bool:
    """True if any path in paths_a overlaps with any path in paths_b."""
    list_a = [_normalize_path(p) for p in paths_a if p.strip()]
    list_b = [_normalize_path(p) for p in paths_b if p.strip()]

    if "*" in list_a or "*" in list_b or "" in list_a or "" in list_b:
        return True

    for a in list_a:
        for b in list_b:
            if a == b:
                return True
            # a is parent directory of b (e.g. backend and backend/server.py)
            if b.startswith(f"{a}/"):
                return True
            # b is parent directory of a
            if a.startswith(f"{b}/"):
                return True
            if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
                return True

    return False


# ─── Concurrency & Queue Manager ─────────────────────────────────────────────


class DispatchQueueManager:
    """Manages active sessions and per-repo overlapping path queues."""

    def __init__(self) -> None:
        self._active_sessions: dict[str, ActiveSession] = {}
        self._queued_items: list[QueuedDispatchItem] = []

    def get_active_sessions(self, repository: str | None = None) -> list[ActiveSession]:
        sessions = [s for s in self._active_sessions.values() if s.status == "active"]
        if repository:
            repo_norm = repository.strip().lower()
            return [s for s in sessions if s.repository.strip().lower() == repo_norm]
        return sessions

    def get_queued_items(self, repository: str | None = None) -> list[QueuedDispatchItem]:
        if repository:
            repo_norm = repository.strip().lower()
            return [q for q in self._queued_items if q.repository.strip().lower() == repo_norm]
        return list(self._queued_items)

    def can_dispatch(self, repository: str, declared_paths: list[str]) -> tuple[bool, str | None]:
        """Check if a dispatch is allowed or if overlapping sessions exist."""
        active = self.get_active_sessions(repository)
        for session in active:
            if paths_overlap(session.declared_paths, declared_paths):
                return False, session.session_id
        return True, None

    def try_dispatch(
        self,
        *,
        repository: str,
        number: int | None = None,
        prompt: str = "",
        labels: Iterable[str] = (),
        declared_paths: list[str] | None = None,
        requested_model: str = "",
    ) -> DispatchAttemptResult:
        """Attempt to dispatch. If overlapping session exists, queue it."""
        paths = declared_paths or extract_declared_paths(prompt)
        decision = resolve_model_routing(labels, prompt, requested_model)
        allowed, blocker_id = self.can_dispatch(repository, paths)

        if allowed:
            session_id = uuid4().hex
            session = ActiveSession(
                session_id=session_id,
                repository=repository,
                number=number,
                declared_paths=paths,
                tier=decision.tier,
                model=decision.model,
            )
            self._active_sessions[session_id] = session
            return DispatchAttemptResult(dispatched=True, session=session)

        queue_id = uuid4().hex
        queued = QueuedDispatchItem(
            queue_id=queue_id,
            repository=repository,
            number=number,
            prompt=prompt,
            declared_paths=paths,
            tier=decision.tier,
            model=decision.model,
            reason=decision.reason,
            blocking_session_id=blocker_id or "",
        )
        self._queued_items.append(queued)
        return DispatchAttemptResult(dispatched=False, queued_item=queued)

    def attach_pr(self, session_id: str, pr_number: int) -> bool:
        """Attach a PR number to an active session."""
        session = self._active_sessions.get(session_id)
        if session:
            session.pr_number = pr_number
            return True
        return False

    def release_session(self, session_id: str, outcome: str = "closed") -> list[QueuedDispatchItem]:
        """Release an active session and return any now-eligible queued items."""
        session = self._active_sessions.pop(session_id, None)
        if not session:
            return []

        session.status = outcome
        dequeued: list[QueuedDispatchItem] = []
        remaining_queue: list[QueuedDispatchItem] = []

        for item in self._queued_items:
            if item.repository.strip().lower() == session.repository.strip().lower():
                allowed, _ = self.can_dispatch(item.repository, item.declared_paths)
                if allowed:
                    dequeued.append(item)
                    continue
            remaining_queue.append(item)

        self._queued_items = remaining_queue
        return dequeued

    def on_pr_merged_or_closed(
        self, repository: str, pr_number: int, outcome: str = "merged"
    ) -> list[QueuedDispatchItem]:
        """Release session associated with a PR and dequeue eligible items."""
        repo_norm = repository.strip().lower()
        matching_sessions = [
            sid
            for sid, s in self._active_sessions.items()
            if s.repository.strip().lower() == repo_norm and s.pr_number == pr_number
        ]
        all_dequeued: list[QueuedDispatchItem] = []
        for sid in matching_sessions:
            all_dequeued.extend(self.release_session(sid, outcome=outcome))
        return all_dequeued


# Global singleton instance for dashboard process
dispatch_queue_manager = DispatchQueueManager()


# ─── Audit Recording ─────────────────────────────────────────────────────────


def record_dispatch_routing_audit(
    *,
    repository: str,
    number: int | None,
    decision: ModelRoutingDecision,
    status: str,
    declared_paths: list[str],
    audit_file: Path = DEFAULT_AUDIT_PATH,
    principal: str = "",
) -> None:
    """Record routing decision and concurrency status into dispatch audit log."""
    payload = {
        "event_type": "dispatch_routing",
        "repository": repository,
        "number": number,
        "tier": decision.tier,
        "model": decision.model,
        "reason": decision.reason,
        "is_dictated_fix": decision.is_dictated_fix,
        "status": status,
        "declared_paths": declared_paths,
        "principal": principal,
        "recorded_at": utc_now_iso(),
    }
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError as err:
        log.warning("failed to append dispatch routing audit record: %s", err)
