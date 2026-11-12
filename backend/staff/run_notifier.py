"""Terminal-run-card notifier: one compact card per terminal run, with dedupe,
digest and quiet hours, so dispatchers never poll run streams (#3052).

Pure planner (:func:`plan`) decides WHAT to send and to WHOM; delivery is a
small port (:class:`Sink`) fanned out by :func:`deliver`. Dedupe reuses the
caller-supplied ``delivered`` set rather than a parallel store (DRY): the
production integration point is the same durable outbox
(:mod:`staff.barb_outbox`, kind ``"run_card"``) that :func:`staff.barb_runtime.enqueue_run_card`
already writes to.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from staff.barb_outbox import OutboxStore
from staff.run_outcome import classify_outcome

#: ~300 tokens, approximated by characters (roughly 4 chars/token).
CARD_CHAR_CAP = 1200
#: More than this many fresh cards inside DIGEST_WINDOW collapse into one digest.
DIGEST_THRESHOLD = 10
DIGEST_WINDOW = timedelta(minutes=10)
#: A card this stale at delivery time (outage recovery) goes in one backlog digest.
BACKLOG_CUTOFF = timedelta(hours=24)
#: Private repos never reach a sink marked public.
PRIVATE_REPOS = frozenset({"Gasification_Model"})


def is_private_repo(repo: str) -> bool:
    return repo in PRIVATE_REPOS or repo.endswith("_Private")


@dataclass(frozen=True)
class RunEvent:
    """One terminal transition. Flat (LoD), matching ``staff.store.RunRecord``'s shape.

    ``always_notify_dieter`` is caller-supplied (a hold-hit or owner-needed
    terminal state, for example): this module classifies verified outcome via
    :func:`staff.run_outcome.classify_outcome`, but it does not own the
    broader fleet vocabulary for *why* a run needs the owner.
    """

    run_id: str
    terminal_state: str
    role: str
    repo: str
    issue_ref: str
    occurred_at: datetime
    status: str = ""
    verification: str = ""
    failure_class: str = ""
    duration_s: float = 0.0
    provider: str = ""
    host: str = ""
    summary: str = ""
    transcript_url: str = ""
    always_notify_dieter: bool = False


@dataclass(frozen=True)
class Notification:
    kind: str  # "card" | "digest" | "backlog_digest"
    run_ids: tuple[str, ...]
    text: str
    notify_dieter: bool
    private: bool


@dataclass(frozen=True)
class NotifierPrefs:
    quiet_hours_start: time = time(0, 0)
    quiet_hours_end: time = time(6, 0)


class Sink:
    """Delivery port. ``public`` sinks never receive a private-repo notification."""

    public: bool = False

    def send(self, notification: Notification) -> None:
        raise NotImplementedError


class OutboxSink(Sink):
    """Delivers through the existing durable run-card outbox (#1800).

    Reuses :class:`~staff.barb_outbox.OutboxStore`'s own (kind, thread, run,
    version) idempotency instead of a parallel dedupe store (DRY): replaying
    the same ``(run_id, terminal_state)`` key only ever reaches this sink once
    per batch (``plan``'s ``delivered`` set), and re-enqueueing the identical
    payload at the same version is itself a no-op in the outbox.
    """

    public = False

    def __init__(self, outbox: OutboxStore, thread_id: str) -> None:
        self._outbox = outbox
        self._thread_id = thread_id

    def send(self, notification: Notification) -> None:
        payload = {"kind": notification.kind, "run_ids": list(notification.run_ids), "text": notification.text}
        self._outbox.enqueue("run_card", self._thread_id, notification.run_ids[0], 1, payload, terminal=True)


def _in_quiet_hours(at: datetime, prefs: NotifierPrefs) -> bool:
    t = at.time()
    start, end = prefs.quiet_hours_start, prefs.quiet_hours_end
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window wraps past midnight


def verified_outcome(event: RunEvent) -> str:
    """The run's outcome bucket (:func:`staff.run_outcome.classify_outcome`), relabelled
    ``"unverified"`` instead of the generic ``"no_op"`` when verification is simply
    missing (as opposed to an explicit ``no_change``/``not_applicable`` verdict).
    """
    bucket = classify_outcome(event.status or event.terminal_state, event.verification, event.failure_class)
    if bucket == "no_op" and not event.verification:
        return "unverified"
    return bucket


def _card_text(event: RunEvent) -> str:
    text = (
        f"{event.role} · {event.repo} {event.issue_ref} · {verified_outcome(event)} "
        f"· {event.duration_s:.0f}s · {event.provider}/{event.host}\n"
        f"{event.summary}\n{event.transcript_url}"
    )
    return text[:CARD_CHAR_CAP]


def _notify_dieter(event: RunEvent, now: datetime, prefs: NotifierPrefs) -> bool:
    if event.always_notify_dieter:
        return True
    if verified_outcome(event) in ("success", "no_op"):
        return False
    return not _in_quiet_hours(now, prefs)


def _card(event: RunEvent, now: datetime, prefs: NotifierPrefs) -> Notification:
    return Notification(
        kind="card",
        run_ids=(event.run_id,),
        text=_card_text(event),
        notify_dieter=_notify_dieter(event, now, prefs),
        private=is_private_repo(event.repo),
    )


def _digest(events: Sequence[RunEvent], now: datetime, prefs: NotifierPrefs, *, kind: str) -> Notification:
    failed = [e for e in events if verified_outcome(e) in ("quality_fail", "infra_fail")]
    names = ", ".join(f"{e.role}/{e.repo}" for e in failed) or "none"
    text = f"{len(events)} runs terminated in the last check; failures: {names}"[:CARD_CHAR_CAP]
    return Notification(
        kind=kind,
        run_ids=tuple(e.run_id for e in events),
        text=text,
        notify_dieter=any(_notify_dieter(e, now, prefs) for e in events),
        private=any(is_private_repo(e.repo) for e in events),
    )


def plan(
    events: Sequence[RunEvent],
    now: datetime,
    prefs: NotifierPrefs,
    *,
    delivered: Collection[tuple[str, str]] = (),
) -> list[Notification]:
    """Decide what to send for a batch of terminal events.

    Pre: ``events`` are terminal transitions; ``delivered`` holds every
    ``(run_id, terminal_state)`` key already notified.
    Post: a key in ``delivered`` never produces a second :class:`Notification`
    (criterion 1); more than :data:`DIGEST_THRESHOLD` fresh events inside
    :data:`DIGEST_WINDOW` of ``now`` collapse into exactly one digest instead
    of one card each (criterion 2); events older than :data:`BACKLOG_CUTOFF`
    collapse into one backlog digest so an outage recovery cannot flood a
    sink; the stale-outage digest and the recent-flood digest are independent
    so a mixed backlog never silently absorbs recent failures.
    """
    fresh = [e for e in events if (e.run_id, e.terminal_state) not in delivered]
    if not fresh:
        return []

    notifications: list[Notification] = []
    backlog = [e for e in fresh if now - e.occurred_at > BACKLOG_CUTOFF]
    if backlog:
        notifications.append(_digest(backlog, now, prefs, kind="backlog_digest"))

    window_start = now - DIGEST_WINDOW
    recent = [e for e in fresh if e not in backlog]
    windowed = [e for e in recent if e.occurred_at >= window_start]
    if len(windowed) > DIGEST_THRESHOLD:
        notifications.append(_digest(windowed, now, prefs, kind="digest"))
        windowed_ids = {e.run_id for e in windowed}
        recent = [e for e in recent if e.run_id not in windowed_ids]

    notifications.extend(_card(event, now, prefs) for event in recent)
    return notifications


def deliver(notification: Notification, sinks: Sequence[Sink]) -> list[Sink]:
    """Fan a notification out to every eligible sink; a public sink is skipped
    for a private-repo notification (criterion 3). Returns the sinks used.
    """
    used = []
    for sink in sinks:
        if notification.private and sink.public:
            continue
        sink.send(notification)
        used.append(sink)
    return used
