"""Read-only Waiting-on-Dieter kanban column assembly (#4321)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Literal

from coordination import board as board_mod
from coordination import claims as claims_mod
from coordination.job_filters import repo_schedule_holds
from kanban_owner_wait import (
    HoldsSyncState,
    build_owner_wait_snapshot,
    hold_entry_link,
    holds_sync_state,
)
from kanban_projection import (
    KanbanAutoMergeSnapshot,
    KanbanCard,
    KanbanClaimSnapshot,
    KanbanColumn,
    KanbanIssueSnapshot,
    KanbanPullRequestSnapshot,
    KanbanReviewsSnapshot,
    KanbanWorkItemSnapshot,
    project_kanban_column,
)
from staff.claim_guard import closing_pr_for, full_repo
from staff.conversations import ConversationStore, get_conversation_store
from staff.holds import Hold, HoldsList
from time_utils import utc_now_iso

log = logging.getLogger("dashboard.kanban.waiting")

SourceKind = Literal["hold", "issue", "approval"]


def _label_names(issue: dict[str, Any]) -> list[str]:
    raw = issue.get("labels") or []
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name:
            names.append(name)
    return names


def _issue_snapshot(issue: dict[str, Any]) -> KanbanIssueSnapshot:
    number = int(issue.get("number") or 0)
    return KanbanIssueSnapshot(
        available=True,
        number=number,
        state=str(issue.get("state") or "open"),
        html_url=str(issue.get("html_url") or ""),
        labels=tuple(_label_names(issue)),
    )


def _pr_snapshot(pr: dict[str, Any]) -> KanbanPullRequestSnapshot:
    head_raw = pr.get("head")
    head: dict[str, Any] = head_raw if isinstance(head_raw, dict) else {}
    return KanbanPullRequestSnapshot(
        available=True,
        number=int(pr.get("number") or 0),
        state=str(pr.get("state") or "open"),
        draft=bool(pr.get("draft")),
        head_sha=str(head.get("sha") or pr.get("head_sha") or ""),
        html_url=str(pr.get("html_url") or ""),
        in_merge_queue=bool(pr.get("merge_queue_entry")),
    )


def _open_issues(repo: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    repo_full = full_repo(repo)
    try:
        raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/issues?state=open&per_page=100")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{repo}: issue list unavailable ({exc})")
        return [], warnings
    if not isinstance(raw, list):
        warnings.append(f"{repo}: issue list returned an invalid payload")
        return [], warnings
    issues = [row for row in raw if isinstance(row, dict) and "pull_request" not in row]
    return issues, warnings


def _linked_pr(repo: str, issue_number: int, cache: dict[str, list[Any]]) -> dict[str, Any] | None:
    repo_full = full_repo(repo)
    pulls = cache.get(repo_full)
    if pulls is None:
        try:
            raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/pulls?state=open&per_page=100")
        except Exception as exc:  # noqa: BLE001
            log.warning("kanban waiting: pulls for %s unavailable: %s", repo_full, exc)
            return None
        pulls = raw if isinstance(raw, list) else []
        cache[repo_full] = pulls
    hit = closing_pr_for(pulls, issue_number)
    return hit if isinstance(hit, dict) else None


def _minimal_open_pr_extras(
    pr: KanbanPullRequestSnapshot,
) -> tuple[KanbanAutoMergeSnapshot | None, KanbanReviewsSnapshot | None]:
    if pr.draft or pr.state.lower() != "open":
        return None, None
    return (
        KanbanAutoMergeSnapshot(available=True, enabled=False),
        KanbanReviewsSnapshot(available=True, head_sha=pr.head_sha, reviews=()),
    )


def _work_item_snapshot(
    owner: str,
    repo: str,
    issue: dict[str, Any],
    scoped_holds: list[Hold],
    approval_reason: str | None,
    pulls_cache: dict[str, list[Any]],
) -> KanbanWorkItemSnapshot:
    labels = _label_names(issue)
    number = int(issue.get("number") or 0)
    issue_url = str(issue.get("html_url") or "")
    owner_wait = build_owner_wait_snapshot(
        issue_labels=labels,
        scoped_holds=scoped_holds,
        approval_reason=approval_reason,
        detail_url=issue_url or None,
    )
    pr_raw = _linked_pr(repo, number, pulls_cache)
    pr_snap = _pr_snapshot(pr_raw) if pr_raw else None
    auto, reviews = (None, None)
    if pr_snap is not None:
        auto, reviews = _minimal_open_pr_extras(pr_snap)
    return KanbanWorkItemSnapshot(
        owner=owner,
        repo=repo,
        issue=_issue_snapshot(issue),
        pull_request=pr_snap,
        claim=KanbanClaimSnapshot(available=True, held=False),
        auto_merge=auto,
        reviews=reviews,
        owner_wait=owner_wait,
    )


def _approval_reason(prop: Any) -> str:
    return f"pending staff.approve ({prop.action})"


def _proposal_issue_tuple(params: Any) -> tuple[str, int] | None:
    if not isinstance(params, dict):
        return None
    repo = params.get("repo") or params.get("repository")
    issue = params.get("issue") or params.get("issue_number") or params.get("number")
    if repo and issue is not None:
        try:
            return str(repo).strip(), int(issue)
        except (TypeError, ValueError):
            return None
    return None


def _pending_approvals_by_issue(store: ConversationStore) -> dict[tuple[str, int], tuple[str, str]]:
    """Map (repo, issue) -> (reason, thread link) for open proposals."""
    out: dict[tuple[str, int], tuple[str, str]] = {}
    try:
        proposals = store.list_proposals(state="proposed", limit=200)
    except Exception as exc:  # noqa: BLE001
        log.warning("kanban waiting: proposals unavailable: %s", exc)
        return out
    for prop in proposals:
        key = _proposal_issue_tuple(prop.params)
        if key is None:
            continue
        link = f"/staff?thread={prop.thread_id}"
        out[key] = (_approval_reason(prop), link)
    return out


def _card_dict(card: KanbanCard) -> dict[str, Any]:
    payload = asdict(card)
    payload["column"] = str(card.column)
    payload["matched_columns"] = [str(c) for c in card.matched_columns]
    payload["stale_reviews"] = [asdict(r) for r in card.stale_reviews]
    payload["sources"] = [asdict(s) for s in card.sources]
    return payload


def _hold_item(hold: Hold, sync: HoldsSyncState) -> dict[str, Any]:
    return {
        "source_kind": "hold",
        "source_label": hold.text,
        "source_url": hold_entry_link(hold),
        "hold_id": hold.id,
        "last_sync_at": sync.last_sync_at,
        "detail": hold.text,
    }


def _approval_item(prop: Any) -> dict[str, Any]:
    title = f"Approval needed: {prop.action}"
    return {
        "source_kind": "approval",
        "source_label": title,
        "source_url": f"/staff?thread={prop.thread_id}",
        "proposal_id": prop.id,
        "detail": _approval_reason(prop),
    }


def build_waiting_on_dieter_column(
    *,
    repo: str | None = None,
    holds_list: HoldsList | None = None,
    conversation_store: ConversationStore | None = None,
) -> dict[str, Any]:
    """Assemble the Waiting-on-Dieter column; fails closed when holds are unreadable."""
    sync = holds_sync_state(holds_list)
    generated_at = utc_now_iso()
    base: dict[str, Any] = {
        "generated_at": generated_at,
        "last_sync_at": sync.last_sync_at,
        "holds_url": sync.holds_url,
        "warnings": [],
    }
    if not sync.readable:
        return {
            **base,
            "column": KanbanColumn.UNKNOWN,
            "column_label": "Unknown",
            "holds_readable": False,
            "items": [],
            "reasons": ("holds list unreadable",),
        }

    repos = [repo] if repo else board_mod.fleet_repos()
    store = conversation_store or get_conversation_store()
    approval_by_issue = _pending_approvals_by_issue(store)
    unmatched_approvals: list[Any] = []
    try:
        proposals = store.list_proposals(state="proposed", limit=200)
        for prop in proposals:
            if _proposal_issue_tuple(prop.params) is None:
                unmatched_approvals.append(prop)
    except Exception:
        pass

    items: list[dict[str, Any]] = []
    seen_holds: set[str] = set()
    pulls_cache: dict[str, list[Any]] = {}
    warnings: list[str] = []

    for repo_name in repos:
        scoped, readable = repo_schedule_holds(repo_name, holds_list)
        if not readable:
            return {
                **base,
                "column": KanbanColumn.UNKNOWN,
                "column_label": "Unknown",
                "holds_readable": False,
                "items": [],
                "reasons": ("holds list unreadable",),
            }
        for hold in scoped:
            if hold.active and hold.kind == "schedule" and hold.id not in seen_holds:
                seen_holds.add(hold.id)
                items.append(_hold_item(hold, sync))

        issues, issue_warnings = _open_issues(repo_name)
        warnings.extend(issue_warnings)
        for issue in issues:
            number = issue.get("number")
            if not isinstance(number, int):
                continue
            approval = approval_by_issue.get((repo_name, number))
            approval_reason = approval[0] if approval else None
            snapshot = _work_item_snapshot(
                "D-sorganization",
                repo_name,
                issue,
                scoped,
                approval_reason,
                pulls_cache,
            )
            card = project_kanban_column(snapshot)
            if card.column != KanbanColumn.WAITING_ON_DIETER:
                continue
            entry: dict[str, Any] = {
                "source_kind": "issue",
                "source_label": f"{repo_name}#{number}",
                "source_url": str(issue.get("html_url") or ""),
                "card": _card_dict(card),
            }
            if approval:
                entry["approval_url"] = approval[1]
            items.append(entry)

    for prop in unmatched_approvals:
        items.append(_approval_item(prop))

    return {
        **base,
        "column": KanbanColumn.WAITING_ON_DIETER,
        "column_label": "Waiting on Dieter",
        "holds_readable": True,
        "items": items,
        "warnings": warnings,
        "reasons": (),
    }
