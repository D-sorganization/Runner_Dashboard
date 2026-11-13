"""Concurrent expedite budget (per repository, default 3)."""

from __future__ import annotations

from escalation.store import EscalationStore, get_escalation_store

DEFAULT_EXPEDITE_CAP = 3


def resolve_cap(requested: int | None) -> int:
    if requested is None or requested < 1:
        return DEFAULT_EXPEDITE_CAP
    return min(requested, 50)


def _repo_matches(stored: str, target: str) -> bool:
    bare = target.split("/")[-1]
    return stored == target or stored == bare or stored.endswith(f"/{bare}")


def active_count_for_repo(repo: str, store: EscalationStore | None = None, *, exclude_pr: int | None = None) -> int:
    st = store or get_escalation_store()
    count = 0
    for row in st.list_active():
        if exclude_pr is not None and row["pr_number"] == exclude_pr and _repo_matches(row["repo"], repo):
            continue
        if _repo_matches(row["repo"], repo):
            count += 1
    return count


def capacity_refused(
    repo: str,
    cap: int,
    pr_number: int,
    store: EscalationStore | None = None,
) -> str | None:
    if active_count_for_repo(repo, store, exclude_pr=pr_number) >= cap:
        return (
            f"Expedite cap reached for {repo} ({cap} concurrent). "
            "Clear an active priority:expedite or wait for CI to finish."
        )
    return None
