"""Terminal-status notification for staff runs (#4269 PR 3, D-06).

``run_status.transition()`` calls :func:`notify_run_terminal` once, on the
compare-and-set that moves a run into a terminal status. The notifier releases
the dispatch session and the claim label, updates the linked work item and
rewrites the run card, so no terminal write can skip that cleanup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from staff.run_status import RunStatus
    from staff.store import RunStore

log = logging.getLogger("dashboard.staff.run_terminal")


def notify_run_terminal(store: RunStore, run_id: str, status: RunStatus | str) -> None:
    """Tell the run notifier that ``run_id`` reached the terminal ``status``.

    Pre: the status write is already committed.
    Post: the notifier saw the stored row once. A notifier failure is logged
    and never raised: the status write stands and the caller carries on.
    """
    value = str(getattr(status, "value", status))
    try:
        from staff.run_link import handle_run_status_change  # noqa: PLC0415

        rec = store.get_run(run_id)
        if rec is None:
            return
        handle_run_status_change(rec, value, summary=getattr(rec, "outcome", "") or None)
    except Exception:  # noqa: BLE001
        log.exception("staff.run_terminal: terminal notification failed for run %s (%s)", run_id, value)
