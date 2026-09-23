"""Repository_Management lease ritual, invoked as subprocesses (never imported).

check_agent_claim → post_agent_lease → agent_communicate register, and the
matching release on exit. Every step is best-effort and recorded as a run
event; only a claim held by someone else stops a run (``blocked``). The
subprocess helper is shared with the coordination API (``coordination.rm_scripts``).
"""

from __future__ import annotations

from coordination.rm_scripts import run_module
from staff.store import RunStore
from staff.workspace import rm_root

LEASE_SKIPPED_NOTE = (
    "Lease ritual could not run from this node; post a `lease:` comment on the issue yourself before editing."
)


def _claim_held(output: str) -> bool:
    compact = output.replace(" ", "").lower()
    return '"held":true' in compact or "held=true" in compact


def acquire(store: RunStore, run_id: str, *, repo: str, issue: str, agent: str, branch: str) -> str | None:
    """Run the ritual. Returns the prompt note, or None when the run is blocked."""
    root = rm_root()
    if root is None:
        store.append_event(run_id, "lease", "Repository_Management checkout not found; lease ritual skipped")
        return LEASE_SKIPPED_NOTE
    session = f"staff-{run_id}"
    res = run_module("check_agent_claim", "--repo", repo, "--issue", issue, root=root)
    store.append_event(run_id, "lease", f"check_agent_claim rc={res.rc}: {res.output[:300]}")
    if _claim_held(res.output):
        store.update_run(run_id, status="blocked", ended_at=_now(), error="issue claim held by another agent")
        store.append_event(run_id, "blocked", "issue claim held by another agent; not starting")
        return None
    res = run_module(
        "post_agent_lease", "--repo", repo, "--issue", issue, "--agent", agent, "--session", session, root=root
    )
    store.append_event(run_id, "lease", f"post_agent_lease rc={res.rc}: {res.output[:300]}")
    res = run_module(
        "agent_communicate",
        *("--repo", repo, "--session", session, "register"),
        *("--agent", agent, "--issue", issue, "--branch", branch),
        root=root,
    )
    store.append_event(run_id, "presence", f"register rc={res.rc}: {res.output[:300]}")
    store.update_run(run_id, lease_id=session)
    return (
        f"The issue lease and presence registration were posted for you (agent={agent}, session={session}); "
        "do not post them again."
    )


def release(store: RunStore, run_id: str, *, repo: str, issue: str, agent: str) -> None:
    root = rm_root()
    current = store.get_run(run_id)
    if root is None or current is None or not current.lease_id:
        return
    session = current.lease_id
    run_module("agent_communicate", "--repo", repo, "--session", session, "release", root=root)
    run_module(
        "release_agent_lease", "--repo", repo, "--issue", issue, "--agent", agent, "--session", session, root=root
    )
    store.append_event(run_id, "lease", "lease and presence released")


def _now() -> str:
    from staff.store import _now as store_now  # noqa: PLC0415

    return store_now()
