"""Scheduler wiring for review-arm (Refs #4249): default off, one approved host, dry-run first.

``start_review_arm`` is called from the staff scheduler bootstrap (leader only,
after startup reconcile). It always publishes a status object; it starts the
loop thread only when ``RD_REVIEW_ARM_HOST`` names this machine. The loop calls
``ReviewArmService.cycle`` every ``interval_seconds``. Dry-run (the default)
decides and logs everything but performs no GitHub write and no dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from staff.review_arm.ledger import Ledger
from staff.review_arm.processor import VerdictProcessor
from staff.review_arm.service import ReviewArmService
from staff.review_arm.settings import Settings

log = logging.getLogger(__name__)

BOX_FILES = {"holds": "arm_holds.txt", "blocked": "blocked_heads.txt", "overrides": "arm_override.txt"}

_lock = threading.Lock()
_service: ReviewArmService | None = None
_settings: Settings | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


def import_box_state(ledger: Ledger, directory: Path) -> dict[str, int]:
    """One-time import of the box's hold/blocked/override files (missing file = empty)."""
    texts = {}
    for key, name in BOX_FILES.items():
        path = directory / name
        texts[key] = path.read_text(encoding="utf-8") if path.is_file() else ""
    counts = ledger.import_box_state(**texts)
    log.info("review-arm box state import from %s: %s", directory, counts)
    return counts


def notify_owner(title: str, text: str, recommended_action: str, dedupe_key: str) -> None:
    """Raise an operator alert through the existing RD paths (fleet event + rd-barb notify)."""
    from fleet_events import FleetEvent, get_event_store  # noqa: PLC0415

    try:
        get_event_store().record(
            FleetEvent(
                ts=int(time.time() * 1000),
                severity="warning",
                kind="watchdog",
                title=title,
                detail=text,
                node=None,
            )
        )
    except Exception:  # noqa: BLE001 — one failed channel must not suppress the other
        log.exception("review-arm fleet event failed: %s", title)
    repo_root = str(Path(__file__).resolve().parents[3])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from scripts.rd_barb_notify import persona_notify  # noqa: PLC0415

        persona_notify(
            "rd-barb",
            {
                "kind": "alert",
                "urgency": "high",
                "text": f"{title}: {text}",
                "recommended_action": recommended_action,
                "dedupe_key": dedupe_key,
            },
        )
    except Exception:  # noqa: BLE001
        log.exception("review-arm rd-barb notify failed: %s", title)


def build_service(settings: Settings, loop: asyncio.AbstractEventLoop | None, *, store: Any = None) -> ReviewArmService:
    """Pre: ``settings.enabled`` implies a running dashboard ``loop``."""
    if not settings.enabled:
        return ReviewArmService(_NoPort(), enabled=False, dry_run=settings.dry_run, host=settings.host)
    if loop is None:
        raise ValueError("an enabled review-arm needs the dashboard event loop")
    from staff.review_arm.hub_port import ProcessorAdapter, RDHubPort  # noqa: PLC0415

    if store is None:
        from staff.runner import get_runner  # noqa: PLC0415

        store = get_runner().store
    ledger = Ledger(settings.ledger_path)
    if settings.box_state_dir is not None:
        import_box_state(ledger, settings.box_state_dir)
    port = RDHubPort(settings=settings, ledger=ledger, loop=loop, store=store, notify=notify_owner)
    processor = VerdictProcessor(
        ledger, ProcessorAdapter(port), dry_run=settings.dry_run, held_prs=lambda: settings.held_prs
    )

    def stall_alert(title: str, text: str) -> None:
        notify_owner(
            title, text, "Check GET /api/staff/review-arm/status and staff dispatch health.", "review-arm:stall"
        )

    return ReviewArmService(
        port,
        enabled=True,
        dry_run=settings.dry_run,
        held_prs=settings.held_prs | ledger.holds(),
        processor=processor,
        max_dispatch=settings.max_dispatch,
        stall_minutes=settings.stall_minutes,
        alert=stall_alert,
        host=settings.host,
    )


class _NoPort:
    """A disabled service never calls its port (see ``ReviewArmService.cycle``)."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"review-arm is disabled; port.{name} must not be called")


def _loop_body(service: ReviewArmService, interval: int) -> None:
    while not _stop.is_set():
        try:
            service.cycle()
        except Exception:  # noqa: BLE001 — the loop survives any single cycle
            log.exception("review-arm cycle crashed")
        _stop.wait(interval)


def start_review_arm(loop: asyncio.AbstractEventLoop | None = None, *, machine: str | None = None) -> bool:
    """Publish status and start the loop on the approved host. Returns whether it started.

    Idempotent: a second call never starts a second loop thread.
    """
    global _service, _settings, _thread  # noqa: PLW0603
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        if machine is None:
            from staff.runner import get_runner  # noqa: PLC0415

            machine = get_runner().machine
        try:
            settings = Settings.from_env(os.environ, machine=machine)
            service = build_service(settings, loop)
        except Exception:  # noqa: BLE001 — bad config disables the service, visibly
            log.exception("review-arm configuration invalid; service disabled")
            settings = Settings()
            service = build_service(settings, None)
        _settings, _service = settings, service
        if not settings.enabled:
            log.info("review-arm disabled on %s (RD_REVIEW_ARM_HOST=%r)", machine, settings.host)
            return False
        _stop.clear()
        _thread = threading.Thread(
            target=_loop_body, args=(service, settings.interval_seconds), name="review-arm", daemon=True
        )
        _thread.start()
        log.info(
            "review-arm started on %s: dry_run=%s interval=%ss", machine, settings.dry_run, settings.interval_seconds
        )
        return True


def stop_review_arm(timeout: float = 5.0) -> None:
    global _thread  # noqa: PLW0603
    _stop.set()
    with _lock:
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout)


def status() -> dict[str, object]:
    with _lock:
        service, settings = _service, _settings
    if service is None:
        return {"role": "pr-remediator", "enabled": False, "dry_run": True, "started": False}
    data = service.status()
    data["started"] = _thread is not None and _thread.is_alive()
    if settings is not None:
        data["interval_seconds"] = settings.interval_seconds
        data["stall_minutes"] = settings.stall_minutes
        data["max_dispatch"] = settings.max_dispatch
    return data
