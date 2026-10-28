"""Repository_Management lease ritual, invoked as subprocesses (never imported).

check_agent_claim → post_agent_lease → agent_communicate register, and the
matching release on exit. Every step is best-effort and recorded as a run
event; only a claim held by someone else stops a run (``blocked``). The
subprocess helper is shared with the coordination API (``coordination.rm_scripts``).
"""

from __future__ import annotations

from datetime import datetime

from coordination.rm_scripts import run_module
from staff import claim_guard
from staff.store import RunRecord, RunStore
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
        store.update_run(
            run_id,
            status="blocked",
            failure_class="lease_blocked",
            ended_at=_now(),
            error="issue claim held by another agent",
        )
        claim_guard.release_claim(run_id, "blocked")
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


def release_reason(rec: RunRecord | None, *, crashed: bool = False) -> str:
    """Describe the recorded outcome; unknown or absent records never imply success."""
    if crashed:
        return "failed: crash"
    if rec is None:
        return "released: status unknown"
    if rec.failure_class == "timeout":
        minutes = "unknown"
        if rec.started_at and rec.ended_at:
            try:
                elapsed = datetime.fromisoformat(rec.ended_at) - datetime.fromisoformat(rec.started_at)
                minutes = f"{max(0.0, elapsed.total_seconds()) / 60:g}"
            except (ValueError, TypeError):
                pass  # Malformed timestamps must not prevent lease cleanup.
        return f"timeout: {minutes} min"
    if rec.status == "succeeded":
        if rec.pr_number:
            outcome = " ".join(rec.outcome.split()) or f"PR #{rec.pr_number} opened"
            return f"completed: {outcome}"
        return "no-op: run finished without a PR"
    if rec.status == "failed":
        return f"failed: {' '.join(rec.failure_class.split()) or 'error'}"
    if rec.status == "cancelled":
        return "cancelled"
    if rec.status == "needs_input":
        return "skipped: needs input"
    return f"released: status {' '.join(rec.status.split()) or 'unknown'}"


def release(store: RunStore, run_id: str, *, repo: str, issue: str, agent: str, reason: str) -> None:
    """Release with an explicit, single-line reason before invoking RM."""
    if not isinstance(reason, str) or not reason.strip() or "\r" in reason or "\n" in reason:
        raise ValueError("reason must be a non-empty single-line string")
    root = rm_root()
    current = store.get_run(run_id)
    if root is None or current is None or not current.lease_id:
        return
    session = current.lease_id
    run_module("agent_communicate", "--repo", repo, "--session", session, "release", root=root)
    run_module(
        "release_agent_lease",
        "--repo",
        repo,
        "--issue",
        issue,
        "--agent",
        agent,
        "--session",
        session,
        "--reason",
        reason,
        root=root,
    )
    store.append_event(run_id, "lease", "lease and presence released")


def _now() -> str:
    from staff.store import _now as store_now  # noqa: PLC0415

    return store_now()
