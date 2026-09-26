"""Reconciliation for staff runs orphaned by dashboard restarts (issue #1293, SC-A4).

Runs execute on daemon threads. If the dashboard restarts (deploy, crash, reboot),
runs in 'queued', 'preparing', or 'running' states stay active in SQLite, blocking
their roles indefinitely.

This module reconciles those orphaned runs at startup:
- Terminates child processes (by PID) if still alive.
- Preserves worktrees with unpushed commits and records their paths on the run.
- Cleans up worktrees that have no unpushed commits.
- Releases RM issue leases (with background retry if RM is unreachable).
- Marks runs as status='failed', failure_class='orphaned'.
- Emits 'staff_run_orphaned' fleet events and surfaces runs in summary.attention.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

from fleet_events import EventStore, FleetEvent, get_event_store
from staff import lease as lease_mod
from staff import verification, workspace
from staff.audit import record_audit
from staff.conversations import ConversationStore, get_conversation_store
from staff.runner import StaffRunner
from staff.store import RunRecord, RunStore, _now
from staff.tokens import revoke_run_token
from staff.watchdog import terminate_process_group

log = logging.getLogger("dashboard.staff.reconcile")


def terminate_pid(pid: int | None) -> None:
    """Check if process PID exists and terminate/kill it and all descendants."""
    if pid is None or pid <= 0:
        return
    terminate_process_group(pid, grace_period=2.0)


def _release_lease_with_retry(
    store: RunStore,
    rec: RunRecord,
    max_retries: int = 3,
    initial_delay: float = 2.0,
) -> None:
    """Attempt lease release synchronously once; if failed, retry in background."""
    if not rec.lease_id:
        return

    issue = rec.target_ref.lstrip("#") if rec.target_kind == "issue" else ""
    agent = rec.provider or "staff"

    def _do_release() -> bool:
        try:
            lease_mod.release(store, rec.id, repo=rec.repo, issue=issue, agent=agent)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Lease release failed for %s (%s); will retry: %s",
                rec.id,
                rec.lease_id,
                exc,
            )
            return False

    if _do_release():
        return

    def _retry_worker() -> None:
        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            time.sleep(delay)
            log.info(
                "Retrying lease release for run %s (attempt %d/%d)",
                rec.id,
                attempt,
                max_retries,
            )
            if _do_release():
                log.info("Lease release succeeded for run %s on attempt %d", rec.id, attempt)
                return
            delay *= 2
        log.error("Exhausted retries releasing lease for run %s (%s)", rec.id, rec.lease_id)

    t = threading.Thread(target=_retry_worker, name=f"lease-retry-{rec.id}", daemon=True)
    t.start()


def reconcile_interrupted_chat_messages(
    conv_store: ConversationStore | None = None,
) -> list[str]:
    """Reconcile chat messages stuck in pending or streaming states across a restart (issue #1491, SC-B1-G8).

    Pre: conv_store is available or default store is reachable.
    Post: All non-terminal reply messages are marked failed with meta.failure_class='interrupted_by_restart',
          a system message offering a retry is posted to each affected thread,
          every state change is audited (SC-A8),
          and user messages are left untouched.
    """
    store = conv_store or get_conversation_store()
    if not store.status.available:
        log.warning("Conversation store is unavailable; skipping chat message reconciliation")
        return []

    try:
        non_terminal = store.list_non_terminal_reply_messages()
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to query non-terminal chat messages: %s", exc)
        return []

    reconciled_ids: list[str] = []
    for msg in non_terminal:
        if msg.author_kind == "user":
            continue

        new_meta = dict(msg.meta)
        new_meta["failure_class"] = "interrupted_by_restart"
        new_meta["retryable"] = True

        err_body = msg.body_md if msg.body_md else "Reply interrupted by backend restart."
        store.update_message(
            msg.id,
            delivery="failed",
            kind="error",
            body_md=err_body,
            meta=new_meta,
        )

        retry_meta = {
            "in_reply_to": msg.id,
            "interrupted_message_id": msg.id,
            "failure_class": "interrupted_by_restart",
            "actions": [{"name": "retry", "label": "Retry"}],
            "retryable": True,
            "system_action": "retry_offer",
        }
        retry_body = "The previous reply was interrupted by a backend restart. You can retry your message."
        store.add_message(
            thread_id=msg.thread_id,
            author_kind="system",
            author="system",
            kind="text",
            body_md=retry_body,
            meta=retry_meta,
            delivery="complete",
        )

        try:
            record_audit(
                action="message_reconcile",
                target=f"message:{msg.id}",
                principal="system",
                surface="scheduler",
                thread_id=msg.thread_id,
                outcome="failed",
                detail={
                    "message_id": msg.id,
                    "thread_id": msg.thread_id,
                    "previous_delivery": msg.delivery,
                    "failure_class": "interrupted_by_restart",
                },
                fail_closed=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to record audit for reconciled message %s: %s", msg.id, exc)

        reconciled_ids.append(msg.id)

    if reconciled_ids:
        log.info("Reconciled %d interrupted chat messages: %s", len(reconciled_ids), reconciled_ids)
    return reconciled_ids


def reconcile_orphaned_runs(
    runner: StaffRunner,
    *,
    event_store: EventStore | None = None,
    requeue_queued: bool = False,
    conv_store: ConversationStore | None = None,
) -> list[str]:
    """Reconcile runs left active across a dashboard restart.

    Pre: runner is initialized with a store.
    Post: active runs owned by this machine are marked failed/orphaned,
          leases are released, clean worktrees removed, fleet events emitted,
          and interrupted chat messages reconciled.
    """
    try:
        reconcile_interrupted_chat_messages(conv_store=conv_store)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to reconcile interrupted chat messages: %s", exc, exc_info=True)

    store = runner.store
    active = store.active_runs()
    reconciled_ids: list[str] = []
    ev_store = event_store if event_store is not None else get_event_store()

    for rec in active:
        # Only reconcile runs owned by this node
        if rec.machine and rec.machine != runner.machine:
            continue

        if requeue_queued and rec.status == "queued" and not rec.started_at:
            continue

        # Preserve scheduled retries waiting for backoff (issue #1303, SC-A7)
        if rec.status == "queued" and rec.next_attempt_at:
            continue

        # 1. Terminate child PID if present
        if rec.pid is not None:
            terminate_pid(rec.pid)

        # 2. Check worktree for unpushed commits
        preserved_path: str | None = None
        if rec.workdir:
            wt_path = Path(rec.workdir)
            if wt_path.exists() and wt_path.is_dir():
                if workspace.worktree_has_unpushed_commits(wt_path):
                    preserved_path = str(wt_path)
                    store.append_event(
                        rec.id,
                        "worktree",
                        f"unpushed worktree preserved at {rec.workdir}",
                    )
                else:
                    checkout = workspace.find_repo_checkout(rec.repo) if rec.repo else None
                    workspace.remove_worktree(wt_path, checkout=checkout)
                    store.append_event(rec.id, "worktree", f"clean worktree removed at {rec.workdir}")

        # 3. Release RM lease (with background retry if unreachable)
        _release_lease_with_retry(store, rec)

        # 3b. Revoke active run tokens (SC-E2, issue #1310)
        revoke_run_token(rec.id)

        # 4. Update run record
        err_msg = (
            f"orphaned by dashboard restart; unpushed worktree preserved at {preserved_path}"
            if preserved_path
            else "orphaned by dashboard restart"
        )
        store.update_run(
            rec.id,
            status="failed",
            failure_class="orphaned",
            retryable=False,
            remediation=f"Run was orphaned across dashboard restart on node {runner.machine}; inspect worktree.",
            ended_at=_now(),
            error=err_msg,
        )
        store.append_event(rec.id, "reconcile", err_msg)

        # 5. Emit fleet event
        try:
            ev = FleetEvent(
                ts=int(time.time() * 1000),
                severity="warning",
                kind="staff_run_orphaned",
                title=f"Staff run {rec.id} orphaned",
                detail=(
                    f"Run {rec.id} (role '{rec.role}') on '{rec.repo}' was active across dashboard restart; "
                    "marked failed/orphaned."
                ),
                node=rec.machine,
            )
            ev_store.record(ev)
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to record fleet event for orphaned run %s",
                rec.id,
                exc_info=True,
            )

        reconciled_ids.append(rec.id)

    if reconciled_ids:
        log.info("Reconciled %d orphaned staff runs: %s", len(reconciled_ids), reconciled_ids)
    # Runs that finished while nobody checked their PR are verified now (#1516).
    try:
        verification.recheck_runs(store, machine=runner.machine, opens_pr=runner.opens_pr)
    except Exception:  # noqa: BLE001
        log.warning("Failed to verify finished staff runs on reconcile", exc_info=True)
    return reconciled_ids
