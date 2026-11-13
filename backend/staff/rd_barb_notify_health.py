"""rd-barb notify delivery health (RD #3075): a failed delivery to Dieter is never silent.

Every delivery attempt on the direct path records here. ``status`` reads ``failing`` from the first failure,
``degraded`` once failures have lasted ``DEGRADED_AFTER_S`` (5 minutes, the #3075 banner threshold) and ``ok``
again after one success. An unreadable file reads ``unknown``, never ``ok``. The file holds error codes only,
never message text, addresses or keys.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from staff.rd_barb_memory import rd_barb_state_dir

log = logging.getLogger("dashboard.staff.rd_barb_notify_health")

DEGRADED_AFTER_S = 300.0
MAX_ERROR_CHARS = 200
_FIELDS = ("channel", "consecutive_failures", "failing_since", "last_success_at", "last_attempt_at", "last_error")
_CHANNEL_FIELDS = _FIELDS[1:]


def default_health_path() -> Path:
    return rd_barb_state_dir() / "notify-health.json"


def _one_line(text: str) -> str:
    return " ".join(text.split())[:MAX_ERROR_CHARS]


class NotifyHealth:
    """File-backed delivery health shared by the notifier process and the dashboard API."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def record(self, *, ok: bool, channel: str, now: float, error: str = "") -> None:
        """Record one attempt.

        Pre: ``channel`` is non-empty; a failure carries a non-empty ``error`` code.
        Post: a success clears ``failing_since`` and the failure count; a failure keeps the first ``failing_since``.
        """
        self._check(channel, ok, error)
        with self._lock:
            state = self._read() or {}
            state["channel"] = channel
            self._apply(state, ok=ok, error=error, now=now)
            self._write(state)

    def record_channel(self, name: str, *, ok: bool, now: float, error: str = "") -> None:
        """Record one attempt for a single channel of a fan-out, apart from the aggregate ``record``.

        Pre: ``name`` is non-empty; a failure carries a non-empty ``error`` code.
        Post: ``status()["channels"][name]`` follows the same failing/degraded/ok rules as the aggregate.
        """
        self._check(name, ok, error)
        with self._lock:
            state: dict[str, Any] = self._read() or {}
            channels: dict[str, Any] = state.get("channels") or {}
            entry: dict[str, Any] = dict(channels.get(name) or {})
            self._apply(entry, ok=ok, error=error, now=now)
            state["channels"] = {**channels, name: entry}
            self._write(state)

    @staticmethod
    def _check(channel: str, ok: bool, error: str) -> None:
        if not channel.strip():
            raise ValueError("channel must be non-empty")
        if not ok and not error.strip():
            raise ValueError("a failed attempt needs an error code")

    @staticmethod
    def _apply(state: dict[str, Any], *, ok: bool, error: str, now: float) -> None:
        state["last_attempt_at"] = now
        if ok:
            state.update(consecutive_failures=0, failing_since=None, last_success_at=now, last_error="")
        else:
            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
            state["failing_since"] = state.get("failing_since") or now
            state["last_error"] = _one_line(error)

    def status(self, *, now: float) -> dict[str, Any]:
        """``state`` is never_attempted, ok, failing, degraded or unknown, plus the recorded fields."""
        with self._lock:
            state = self._read()
        out: dict[str, Any] = {key: (state or {}).get(key) for key in _FIELDS}
        out["degraded_after_s"] = DEGRADED_AFTER_S
        try:
            out["consecutive_failures"] = int(out["consecutive_failures"] or 0)
            out["state"] = "unknown" if state is None else self._classify(out, now)
            out["channels"] = self._channel_states((state or {}).get("channels"), now)
        except (TypeError, ValueError):  # a hand-edited file is unknown, never ok
            out["state"] = "unknown"
        return out

    @classmethod
    def _channel_states(cls, channels: Any, now: float) -> dict[str, dict[str, Any]]:
        """Per-channel ``state`` and recorded fields for a fan-out; empty when only one channel ever sent."""
        out: dict[str, dict[str, Any]] = {}
        for name, entry in (channels if isinstance(channels, dict) else {}).items():
            fields = {key: entry.get(key) for key in _CHANNEL_FIELDS}
            fields["consecutive_failures"] = int(fields["consecutive_failures"] or 0)
            out[str(name)] = {**fields, "state": cls._classify(fields, now)}
        return out

    @staticmethod
    def _classify(fields: dict[str, Any], now: float) -> str:
        if fields["last_attempt_at"] is None:
            return "never_attempted"
        since = fields["failing_since"]
        if since is None:
            return "ok"
        return "degraded" if now - float(since) >= DEGRADED_AFTER_S else "failing"

    def _read(self) -> dict[str, Any] | None:
        """The stored fields; ``{}`` when no file exists yet, ``None`` when it cannot be read."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.error("rd-barb notify health unreadable: %s", type(exc).__name__)
            return None
        return data if isinstance(data, dict) else None

    def _write(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.tmp")  # one temp file per writer process
        kept = {key: state.get(key) for key in _FIELDS}
        kept["channels"] = state.get("channels") or {}
        tmp.write_text(json.dumps(kept), encoding="utf-8")
        tmp.replace(self._path)
