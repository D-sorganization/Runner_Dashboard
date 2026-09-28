"""Waiting on you Inbox and Barb Briefings Engine (SC-C5, Issue #1328).

Aggregates actionable items requiring operator/owner attention:
- Pending action proposals awaiting approval (SC-B6 #1313)
- Needs-input questions from stalled/clarifying agent runs (SC-B7 #1314)
- Escalated work items from Barb's follow-up engine (SC-C3 #1316)
- Decisions needed recorded in repository STATUS.md charters (Issue #1199)
- Board proposals awaiting the owner
- Provider authentication sign-ins required

Fault-tolerant: Any failing source reports 'unavailable' with error detail
while surviving sources continue to aggregate cleanly.
"""

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from push import send_push
from staff.briefings import generate_briefing, post_briefing_to_barb
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
)
from staff.store import RunStore
from staff.store import get_store as get_run_store
from staff.work_items import WorkItemStore, get_work_item_store
from time_utils import utc_now_iso

__all__ = [
    "InboxAggregate",
    "InboxItem",
    "InboxSource",
    "SourceStatus",
    "collect_inbox",
    "generate_briefing",
    "post_briefing_to_barb",
    "send_escalation_push",
]

log = logging.getLogger("dashboard.staff.inbox")

InboxSource = Literal[
    "approval",
    "needs_input",
    "escalation",
    "project_decision",
    "board_proposal",
    "auth_sign_in",
]


@dataclass(frozen=True)
class InboxItem:
    """A single item requiring owner/operator attention."""

    id: str
    source: InboxSource
    title: str
    summary: str
    severity: Literal["low", "medium", "high", "critical"]
    created_at: str
    link: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # Decision SLA (WP-2.6, #1607): when the owner must decide, and what Barb applies if silent.
    decide_by: str | None = None
    default_if_silent: str = ""
    details: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceStatus:
    """Status of an individual inbox aggregation source."""

    status: Literal["ok", "unavailable"]
    count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InboxAggregate:
    """Full aggregated inbox payload."""

    items: list[InboxItem]
    counts: dict[str, int]
    sources: dict[str, SourceStatus]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        item_dicts = [item.to_dict() for item in self.items]
        return {
            "items": item_dicts,
            "inbox": item_dicts,
            "count": self.counts["total"],
            "counts": self.counts,
            "sources": {k: v.to_dict() for k, v in self.sources.items()},
            "generated_at": self.generated_at,
        }


APPROVAL_PROMPT_EXCERPT = 160


def _approval_text(prop_id: str, action: str, thread_id: str, params: Any) -> tuple[str, str]:
    """Title and summary that tell one proposal from another (#1712).

    A dispatch names its role and repo in the title; every summary ends with the proposal id.
    """
    p = params if isinstance(params, dict) else {}
    role, repo = str(p.get("role") or ""), str(p.get("repo") or "")
    title = f"Approval needed: {action}"
    if role:
        title += f" · {role}" + (f" on {repo}" if repo else "")
    detail = str(p.get("rationale") or p.get("prompt") or "") or f"Action {action} proposed for thread {thread_id}"
    if len(detail) > APPROVAL_PROMPT_EXCERPT:
        detail = detail[: APPROVAL_PROMPT_EXCERPT - 1].rstrip() + "…"
    return title, f"{detail} (proposal {prop_id})"


def _canonical_json(params: Any) -> str:
    """Canonical JSON encoding of params for duplicate detection."""
    try:
        return json.dumps(params, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(params)


def _collect_approvals(c_store: ConversationStore) -> list[InboxItem]:
    """Collect pending proposals awaiting approval, collapsing identical requests."""
    proposals = c_store.list_proposals(state="proposed", limit=100)
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for prop in proposals:
        key = (prop.action, prop.thread_id, _canonical_json(prop.params))
        groups.setdefault(key, []).append(prop)

    items: list[InboxItem] = []
    for group in groups.values():
        primary = group[0]
        sev: Literal["low", "medium", "high", "critical"] = "medium"
        if primary.risk == "high":
            sev = "high"
        elif primary.risk == "low":
            sev = "low"
        title, summary = _approval_text(primary.id, primary.action, primary.thread_id, primary.params)

        extra = len(group) - 1
        details: list[str] = [p.id for p in group] if extra > 0 else []
        if extra > 0:
            summary = f"{summary} (and {extra} identical requests)"

        items.append(
            InboxItem(
                id=f"approval_{primary.id}",
                source="approval",
                title=title,
                summary=summary,
                severity=sev,
                created_at=primary.created_at,
                link=f"/staff?thread={primary.thread_id}",
                metadata={
                    "proposal_id": primary.id,
                    "proposal_ids": [p.id for p in group],
                    "duplicate_count": extra,
                    "thread_id": primary.thread_id,
                    "action": primary.action,
                    "risk": primary.risk,
                    "params": primary.params,
                },
                decide_by=primary.decide_by,
                default_if_silent=primary.default_if_silent,
                details=details,
            )
        )
    return items


def _collect_needs_input(
    r_store: RunStore,
    w_store: WorkItemStore,
    c_store: ConversationStore | None = None,
    caller_id: str | None = None,
) -> list[InboxItem]:
    """Collect agent runs, work items, and unread threads stopped waiting for user input."""
    items: list[InboxItem] = []
    seen_runs: set[str] = set()

    # 1. Runs with status="needs_input"
    runs = r_store.list_runs(status="needs_input", limit=50)
    for run in runs:
        seen_runs.add(run.id)
        q = getattr(run, "outcome", "") or getattr(run, "last_line", "") or ""
        items.append(
            InboxItem(
                id=f"needs_input_{run.id}",
                source="needs_input",
                title=f"Question from {run.role}",
                summary=q or run.prompt[:200],
                severity="medium",
                created_at=run.created_at,
                link=f"/staff?run={run.id}",
                metadata={"run_id": run.id, "role": run.role, "repo": run.repo, "question": q},
            )
        )

    # 2. Work items with state="waiting_on_user"
    wis = w_store.list_work_items(state="waiting_on_user", limit=50)
    for wi in wis:
        if any(run_id in seen_runs for run_id in wi.links.get("runs", [])):
            continue
        link = f"/staff?thread={wi.thread_id}" if wi.thread_id else f"/staff?work_item={wi.id}"
        items.append(
            InboxItem(
                id=f"waiting_wi_{wi.id}",
                source="needs_input",
                title=f"Input needed: {wi.title}",
                summary=f"Work item {wi.id} waiting on user input",
                severity="medium",
                created_at=wi.updated_at,
                link=link,
                metadata={"work_item_id": wi.id, "owner_role": wi.owner_role},
            )
        )

    # 3. Unread threads waiting on user attention
    if c_store is not None:
        try:
            open_threads = c_store.list_threads(status="open", limit=50)
            for th in open_threads:
                has_unread = (
                    th.unread_counters.get(caller_id, 0) > 0
                    if caller_id
                    else any(cnt > 0 for cnt in th.unread_counters.values())
                )
                if has_unread:
                    items.append(
                        InboxItem(
                            id=th.id,
                            source="needs_input",
                            title=f"Unread in {th.title}",
                            summary=f"Unread message(s) in thread {th.title}",
                            severity="medium",
                            created_at=th.updated_at or th.created_at,
                            link=f"/staff?thread={th.id}",
                            metadata={
                                "thread_id": th.id,
                                "unread_count": th.unread_counters.get(caller_id or "", 0),
                            },
                        )
                    )
        except Exception as exc:
            log.warning("Inbox failed collecting unread threads: %s", exc)

    return items


def _collect_escalations(w_store: WorkItemStore) -> list[InboxItem]:
    """Collect escalated work items requiring immediate owner attention."""
    items: list[InboxItem] = []
    wis = w_store.list_work_items(state="escalated", limit=50)
    for wi in wis:
        link = f"/staff?thread={wi.thread_id}" if wi.thread_id else f"/staff?work_item={wi.id}"
        items.append(
            InboxItem(
                id=f"escalation_{wi.id}",
                source="escalation",
                title=f"Escalated: {wi.title}",
                summary=f"Work item {wi.id} escalated to Barb / Owner",
                severity="critical",
                created_at=wi.updated_at,
                link=link,
                metadata={"work_item_id": wi.id, "owner_role": wi.owner_role},
            )
        )
    return items


def github_inbox_sources_enabled() -> bool:
    """False when ``STAFF_INBOX_GITHUB_SOURCES=0`` disables the GitHub-backed
    inbox sources (project decisions, board proposals). Used by the hermetic
    staff e2e harness (#1556) so the inbox never reaches api.github.com."""
    return os.environ.get("STAFF_INBOX_GITHUB_SOURCES", "1").lower() not in {"0", "false", "no", ""}


async def _collect_project_decisions() -> list[InboxItem]:
    """Collect decisions needed from repository STATUS.md project charters (one item per repo)."""
    items: list[InboxItem] = []
    from projects import service as proj_service

    repos = proj_service.configured_repos()
    # Concurrent, as in fleet_overview: awaiting ~40 repos in turn took 17-26 s cold.
    overviews = await asyncio.gather(*(proj_service.project_overview(repo) for repo in repos))
    for repo, overview in zip(repos, overviews, strict=True):
        decisions = overview.get("decisions_needed", [])
        if not decisions:
            continue
        count = len(decisions)
        title = f"{repo}: 1 decision needed" if count == 1 else f"{repo}: {count} decisions needed"
        summary = f"{count} decision{'s' if count != 1 else ''} needed in {repo} STATUS.md charter."
        is_urgent = any("urgent" in str(dec).lower() for dec in decisions)
        severity: Literal["low", "medium", "high", "critical"] = "high" if is_urgent else "low"
        decision_strings = [str(dec) for dec in decisions]

        items.append(
            InboxItem(
                id=f"project_dec_{repo}",
                source="project_decision",
                title=title,
                summary=summary,
                severity=severity,
                created_at=overview.get("generated_at") or utc_now_iso(),
                link=f"/projects/{repo}",
                metadata={"repo": repo, "decisions": decision_strings, "count": count},
                details=decision_strings,
            )
        )
    return items


async def _collect_board_proposals() -> list[InboxItem]:
    """Collect open Board proposals awaiting decision from CR-7 store (WP-0.2, #1475)."""
    from cache_utils import cache_get, cache_set
    from dashboard_config import DEFAULT_CACHE_TTL
    from proposals import store as prop_store

    cache_key = "inbox:board_proposals:open"
    issues = cache_get(cache_key, DEFAULT_CACHE_TTL)
    if issues is None:
        issues = await prop_store.list_github_proposals(state="open")
        cache_set(cache_key, issues)

    items: list[InboxItem] = []
    for issue in issues:
        state = issue.get("state", "open")
        if state == "closed":
            continue
        decision, _ = prop_store.extract_decision_info(issue)
        if decision is not None:
            continue

        number = int(issue.get("number", 0))
        title = issue.get("title", f"Board Proposal #{number}")
        body = issue.get("body") or ""
        parsed = prop_store.parse_proposal_markdown(body)

        urgency = (parsed.get("urgency") or "").lower()
        if urgency == "emergency":
            sev: Literal["low", "medium", "high", "critical"] = "critical"
        elif urgency == "urgent":
            sev = "high"
        elif urgency == "routine":
            sev = "medium"
        else:
            sev = "medium"

        summary = parsed.get("problem") or f"Board proposal #{number} awaiting decision"
        created_at = issue.get("created_at") or utc_now_iso()

        items.append(
            InboxItem(
                id=f"board_proposal_{number}",
                source="board_proposal",
                title=title,
                summary=summary,
                severity=sev,
                created_at=created_at,
                link="/staff/fleet-command?section=proposals",
                metadata={
                    "kind": "board_proposal",
                    "proposal_number": number,
                    "target_repos": parsed.get("target_repos", []),
                    "urgency": parsed.get("urgency", ""),
                    "estimated_cost": parsed.get("estimated_cost", ""),
                    "html_url": issue.get("html_url", ""),
                },
            )
        )
    return items


def _auth_sign_in_severity(probes: dict[str, Any]) -> Literal["medium", "high"]:
    """Severity of the aggregated sign-in item.

    ``high`` only when no enabled, non-future provider used by a dispatchable
    role is signed in (nothing can run); otherwise ``medium``.
    """
    from agent_remediation import PROVIDER_REGISTRY
    from provider_switch import canonical_id, is_disabled

    try:
        from staff.roles import load_roles

        role_providers = {canonical_id(p) for r in load_roles().values() if r.dispatchable for p in r.providers}
    except Exception as exc:  # roles unreadable: judge by every enabled provider
        log.debug("Auth severity could not read roles: %s", exc)
        role_providers = set()

    for entry in PROVIDER_REGISTRY:
        if not entry.enabled or entry.dispatch_mode == "future":
            continue
        can_id = canonical_id(entry.dashboard_id)
        if role_providers and can_id not in role_providers:
            continue
        if is_disabled(entry.dashboard_id) or is_disabled(can_id):
            continue
        avail = probes.get(entry.dashboard_id) or {}
        if avail.get("installed") and avail.get("authenticated"):
            return "medium"
    return "high"


def _collect_auth_sign_ins() -> list[InboxItem]:
    """Collect providers requiring authentication sign-in into a single aggregated item."""
    items: list[InboxItem] = []
    try:
        from agent_remediation import PROVIDER_REGISTRY
        from agent_remediation.provider_probe import probe_provider_availability

        probes = probe_provider_availability()
        needing_sign_in: list[tuple[Any, str]] = []
        for entry in PROVIDER_REGISTRY:
            # A "future" provider is never dispatched to, so its sign-in is nobody's action.
            if not entry.enabled or entry.dispatch_mode == "future":
                continue
            avail = probes.get(entry.dashboard_id)
            if avail and avail.get("installed") and not avail.get("authenticated"):
                detail = avail.get("detail", "")
                needing_sign_in.append((entry, detail))

        if not needing_sign_in:
            return []

        count = len(needing_sign_in)
        title = "1 provider needs sign-in" if count == 1 else f"{count} providers need sign-in"
        names = ", ".join(entry.label for entry, _ in needing_sign_in)
        summary = f"Sign-in required for: {names}. Visit Settings > Credentials to authenticate."
        severity = _auth_sign_in_severity(probes)
        details = [
            {"provider": entry.dashboard_id, "label": entry.label, "reason": detail}
            for entry, detail in needing_sign_in
        ]

        items.append(
            InboxItem(
                id="auth_sign_in",
                source="auth_sign_in",
                title=title,
                summary=summary,
                severity=severity,
                created_at=utc_now_iso(),
                link="/settings#credentials",
                metadata={
                    "provider_ids": [entry.dashboard_id for entry, _ in needing_sign_in],
                    "details": details,
                },
                details=details,
            )
        )
    except Exception as exc:
        log.warning("Failed probing provider auth sign-ins: %s", exc)
        raise
    return items


def _parse_created_at_epoch(iso_str: str) -> float:
    """Parse ISO timestamp to seconds since epoch for sorting (newest first)."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


async def collect_inbox(
    c_store: ConversationStore | None = None,
    r_store: RunStore | None = None,
    w_store: WorkItemStore | None = None,
    caller_id: str | None = None,
) -> InboxAggregate:
    """Aggregate all waiting-on-you items with per-source fault isolation."""
    c_store = c_store or get_conversation_store()
    r_store = r_store or get_run_store()
    w_store = w_store or get_work_item_store()

    all_items: list[InboxItem] = []
    sources: dict[str, SourceStatus] = {}
    counts: dict[str, int] = {
        "total": 0,
        "approvals": 0,
        "needs_input": 0,
        "escalations": 0,
        "project_decisions": 0,
        "project_decision_repos": 0,
        "board_proposals": 0,
        "auth_sign_ins": 0,
    }

    # 1. Approvals
    try:
        appr_items = _collect_approvals(c_store)
        all_items.extend(appr_items)
        counts["approvals"] = len(appr_items)
        sources["approvals"] = SourceStatus(status="ok", count=len(appr_items))
    except Exception as exc:
        log.exception("Inbox failed collecting approvals: %s", exc)
        sources["approvals"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 2. Needs input
    try:
        ni_items = _collect_needs_input(r_store, w_store, c_store, caller_id)
        all_items.extend(ni_items)
        counts["needs_input"] = len(ni_items)
        sources["needs_input"] = SourceStatus(status="ok", count=len(ni_items))
    except Exception as exc:
        log.exception("Inbox failed collecting needs_input: %s", exc)
        sources["needs_input"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 3. Escalations
    try:
        esc_items = _collect_escalations(w_store)
        all_items.extend(esc_items)
        counts["escalations"] = len(esc_items)
        sources["escalations"] = SourceStatus(status="ok", count=len(esc_items))
    except Exception as exc:
        log.exception("Inbox failed collecting escalations: %s", exc)
        sources["escalations"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 4. Project Decisions
    github_sources = github_inbox_sources_enabled()
    if github_sources:
        try:
            pd_items = await _collect_project_decisions()
            all_items.extend(pd_items)
            counts["project_decisions"] = sum(len(item.details) for item in pd_items)
            counts["project_decision_repos"] = len(pd_items)
            sources["project_decisions"] = SourceStatus(status="ok", count=counts["project_decisions"])
        except Exception as exc:
            log.warning("Inbox failed collecting project_decisions: %s", exc)
            sources["project_decisions"] = SourceStatus(status="unavailable", count=0, error=str(exc))
    else:
        sources["project_decisions"] = SourceStatus(status="ok", count=0)

    # 5. Board Proposals (CR-7 store, issue #1475)
    if github_sources:
        try:
            bp_items = await _collect_board_proposals()
            all_items.extend(bp_items)
            counts["board_proposals"] = len(bp_items)
            sources["board_proposals"] = SourceStatus(status="ok", count=len(bp_items))
        except Exception as exc:
            log.warning("Inbox failed collecting board_proposals: %s", exc)
            sources["board_proposals"] = SourceStatus(status="unavailable", count=0, error=str(exc))
    else:
        sources["board_proposals"] = SourceStatus(status="ok", count=0)

    # 6. Auth sign-ins
    try:
        auth_items = _collect_auth_sign_ins()
        all_items.extend(auth_items)
        # One aggregated item; the count is the number of providers needing sign-in.
        counts["auth_sign_ins"] = sum(len(item.details) for item in auth_items)
        sources["auth_sign_ins"] = SourceStatus(status="ok", count=counts["auth_sign_ins"])
    except Exception as exc:
        log.warning("Inbox failed collecting auth_sign_ins: %s", exc)
        sources["auth_sign_ins"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    counts["total"] = len(all_items)

    # Sort descending by severity (critical, high, medium, low),
    # approvals first within equal severity, then newest first
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_items.sort(
        key=lambda it: (
            sev_rank.get(it.severity, 4),
            0 if it.source == "approval" else 1,
            -_parse_created_at_epoch(it.created_at),
        )
    )

    return InboxAggregate(
        items=all_items,
        counts=counts,
        sources=sources,
        generated_at=utc_now_iso(),
    )


async def send_escalation_push(item: InboxItem) -> int:
    """Send web push notification for critical escalations with deep links."""
    payload = {
        "title": f"🚨 {item.title}",
        "body": item.summary[:200],
        "deep_link": item.link,
        "topic": "staff.escalation",
        "severity": item.severity,
        "item_id": item.id,
        "timestamp": item.created_at,
    }
    try:
        res = await send_push(topic="staff.escalation", payload=payload)
        return int(res.get("sent", 0))
    except Exception as exc:
        log.warning("Failed sending escalation push for %s: %s", item.id, exc)
        return 0
