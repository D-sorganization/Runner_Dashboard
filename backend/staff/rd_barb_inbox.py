"""rd-barb dashboard inbox (RD #3075): the dashboard half of Dieter's "dashboard plus email" notify channel.

Each delivered question or alert is appended here and read by ``GET /api/v1/staff/rd-barb/inbox``. The file keeps
only the newest ``MAX_ITEMS`` items. An envelope that carries a secret, a private address or a confidential
marker is refused whole and never written, so the inbox holds no more than the guidelines doc may hold.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from staff.rd_barb_memory import rd_barb_state_dir
from staff.redaction import redact_sensitive_content

log = logging.getLogger("dashboard.staff.rd_barb_inbox")

MAX_ITEMS = 100
MAX_TEXT_CHARS = 2000
_TEXT_FIELDS = ("summary", "body", "recommendation_reason", "recommended_action")
_CONFIDENTIAL_RE = re.compile(r"security[-_ ]confidential", re.IGNORECASE)


class InboxRefused(ValueError):
    """An envelope was refused (secret or confidential marker); nothing was written."""


def default_inbox_path() -> Path:
    return rd_barb_state_dir() / "inbox.json"


def _option_labels(msg: dict[str, Any]) -> list[str]:
    return [str(o["label"]) for o in msg.get("options") or [] if isinstance(o, dict) and "label" in o]


def _texts(msg: dict[str, Any]) -> Iterable[str]:
    yield from (v for f in _TEXT_FIELDS if isinstance(v := msg.get(f), str))
    yield from _option_labels(msg)


def _item_id(dedupe_key: str) -> str:
    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:16]


class DashboardInbox:
    """File-backed, bounded inbox shared by the notifier process and the dashboard API."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time, max_items: int = MAX_ITEMS) -> None:
        if max_items < 1:
            raise ValueError("max_items must be at least 1")
        self._path, self._clock, self._max = path, clock, max_items
        self._lock = threading.Lock()

    def add(self, msg: dict[str, Any]) -> None:
        """Append one validated envelope; a repeat of the same ``dedupe_key`` is stored once.

        Pre: ``msg`` passed ``rd_barb_notify.validate``. Raises ``InboxRefused`` (secret) or ``OSError``.
        Post: the file holds at most ``max_items`` items, oldest dropped first.
        """
        for text in _texts(msg):
            if _CONFIDENTIAL_RE.search(text) or redact_sensitive_content(text) != text:
                raise InboxRefused("envelope carries a secret, address or confidential marker")
        item = {
            **{f: str(msg.get(f) or "")[:MAX_TEXT_CHARS] for f in _TEXT_FIELDS},
            "id": _item_id(str(msg["dedupe_key"])),
            "kind": msg["kind"],
            "urgency": msg["urgency"],
            "options": _option_labels(msg),
            "created_at": self._clock(),
            "acknowledged": False,
        }
        with self._lock:
            items = self._read()
            if any(existing.get("id") == item["id"] for existing in items):
                return
            self._write((items + [item])[-self._max :])

    def unacknowledged(self) -> list[dict[str, Any]]:
        """Unread items, newest first."""
        with self._lock:
            items = self._read()
        return [i for i in reversed(items) if not i.get("acknowledged")]

    def acknowledge(self, item_id: str) -> bool:
        """Mark one item read; False when no such item exists."""
        with self._lock:
            items = self._read()
            hit = [i for i in items if i.get("id") == item_id]
            for item in hit:
                item["acknowledged"] = True
            if hit:
                self._write(items)
            return bool(hit)

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.error("rd-barb inbox unreadable: %s", type(exc).__name__)
            return []
        return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.tmp")  # one temp file per writer process
        tmp.write_text(json.dumps(items), encoding="utf-8")
        tmp.replace(self._path)
