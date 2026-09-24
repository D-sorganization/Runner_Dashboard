"""Staff process watchdog: enforces wall-clock and idle timeouts (issue #1294, SC-A5).

Operates independently of stdout line pumping. If a provider CLI hangs silently
(waiting on auth, a prompt, or the network), or overruns its wall-clock budget, this
watchdog terminates the process tree, records failure classification ('timeout',
'stalled', or 'unkillable'), and emits periodic heartbeat events.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

from staff.store import RunStore, _now

log = logging.getLogger("dashboard.staff.watchdog")


def terminate_process_group(pid: int | None, grace_period: float = 3.0) -> bool:
    """Terminate and, if needed, kill a process and all its descendants.

    Returns True if all processes are confirmed dead, False if any survived.
    """
    if pid is None or pid <= 0:
        return True

    if psutil is not None:
        try:
            if not psutil.pid_exists(pid):
                return True
            parent = psutil.Process(pid)
            try:
                children = parent.children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                children = []
            all_procs = children + [parent]

            for p in all_procs:
                try:
                    p.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            gone, alive = psutil.wait_procs(all_procs, timeout=grace_period)
            for p in alive:
                try:
                    p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            gone2, still_alive = psutil.wait_procs(alive, timeout=1.0)
            return len(still_alive) == 0
        except Exception as exc:  # noqa: BLE001
            log.warning("terminate_process_group failed for PID %d: %s", pid, exc)
            return False

    # Fallback when psutil is not available
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(min(grace_period, 0.5))
        try:
            kill_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(pid, kill_sig)
        except (ProcessLookupError, OSError):
            pass
        return True
    except (ProcessLookupError, OSError):
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Fallback process termination failed for PID %d: %s", pid, exc)
        return False


class StaffWatchdog:
    """Independent background watchdog for an active Staff run subprocess.

    Monitors:
    1. Wall-clock duration against ``max_seconds`` (default from role ``budget.max_minutes``).
    2. Idle duration against ``idle_seconds`` (default from role ``idle_minutes``).
    3. Emits 'heartbeat' events every ``heartbeat_interval`` seconds.
    """

    def __init__(
        self,
        run_id: str,
        proc: subprocess.Popen[Any],
        store: RunStore,
        max_seconds: float = 4 * 3600.0,
        idle_seconds: float = 20 * 60.0,
        heartbeat_interval: float = 60.0,
        grace_period: float = 3.0,
        check_interval: float = 0.2,
        time_fn: Callable[[], float] = time.monotonic,
        now_iso_fn: Callable[[], str] = _now,
    ) -> None:
        self.run_id = run_id
        self.proc = proc
        self.store = store
        self.max_seconds = max_seconds
        self.idle_seconds = idle_seconds
        self.heartbeat_interval = heartbeat_interval
        self.grace_period = grace_period
        self.check_interval = check_interval
        self._time_fn = time_fn
        self._now_iso_fn = now_iso_fn

        now = self._time_fn()
        self.started_at: float = now
        self.last_output_at: float = now
        self.last_heartbeat_at: float = now

        self.failure_class: str | None = None
        self.error_message: str | None = None

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        now = self._time_fn()
        with self._lock:
            self.started_at = now
            self.last_output_at = now
            self.last_heartbeat_at = now
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"watchdog-{self.run_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def record_output(self) -> None:
        """Invoked on each line/chunk received from the child process stdout."""
        now = self._time_fn()
        with self._lock:
            self.last_output_at = now

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.proc.poll() is not None:
                break

            now = self._time_fn()
            with self._lock:
                elapsed = now - self.started_at
                idle_elapsed = now - self.last_output_at
                last_output = self.last_output_at
                heartbeat_elapsed = now - self.last_heartbeat_at

            # 1. Wall-clock deadline check
            if elapsed > self.max_seconds:
                self._handle_expiry(
                    reason="timeout",
                    msg=f"run exceeded wall-clock limit of {self.max_seconds:.0f}s; terminated",
                )
                break

            # 2. Idle deadline check
            if idle_elapsed > self.idle_seconds:
                self._handle_expiry(
                    reason="stalled",
                    msg=f"run stalled: no output for {self.idle_seconds:.0f}s; terminated",
                )
                break

            # 3. Heartbeat check
            if heartbeat_elapsed >= self.heartbeat_interval:
                self._emit_heartbeat(elapsed, last_output)
                with self._lock:
                    self.last_heartbeat_at = now

            self._stop_event.wait(self.check_interval)

    def _emit_heartbeat(self, elapsed: float, last_output_monotonic: float) -> None:
        now_dt = datetime.now(UTC)
        offset = self._time_fn() - last_output_monotonic
        last_out_dt = datetime.fromtimestamp(now_dt.timestamp() - offset, tz=UTC)
        payload = json.dumps(
            {
                "elapsed": round(elapsed, 1),
                "last_output_at": last_out_dt.isoformat().replace("+00:00", "Z"),
            }
        )
        try:
            self.store.append_event(self.run_id, "heartbeat", payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to record heartbeat event for run %s: %s", self.run_id, exc)

    def _handle_expiry(self, reason: str, msg: str) -> None:
        log.warning("Staff run %s expired (%s): %s", self.run_id, reason, msg)
        pid = self.proc.pid
        ok = terminate_process_group(pid, grace_period=self.grace_period)
        if not ok:
            unkillable_msg = (
                f"failed to terminate process group (PID {pid}) after {reason} expiry; process is unkillable"
            )
            log.error("Staff run %s: %s", self.run_id, unkillable_msg)
            self.failure_class = "unkillable"
            self.error_message = unkillable_msg
            self.store.append_event(self.run_id, "error", unkillable_msg)
            self.store.update_run(
                self.run_id,
                status="failed",
                failure_class="unkillable",
                error=unkillable_msg,
                ended_at=self._now_iso_fn(),
            )
            try:
                from fleet_events import FleetEvent, get_event_store

                get_event_store().record(
                    FleetEvent(
                        ts=int(time.time() * 1000),
                        severity="critical",
                        kind="watchdog",
                        title=f"Staff run {self.run_id} process unkillable",
                        detail=unkillable_msg,
                    )
                )
            except Exception:
                log.warning(
                    "Failed to emit critical fleet event for %s",
                    self.run_id,
                    exc_info=True,
                )
            return

        self.failure_class = reason
        self.error_message = msg
        self.store.append_event(self.run_id, reason, msg)
        self.store.update_run(self.run_id, failure_class=reason)
