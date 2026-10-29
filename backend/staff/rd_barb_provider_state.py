"""Persist the active rd-barb subscription CLI provider (#3527)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.rd_barb_memory import rd_barb_state_dir

log = logging.getLogger("dashboard.staff.rd_barb_provider_state")

STATE_FILE = "provider-active.json"
_lock = threading.Lock()


@dataclass(frozen=True)
class ActiveProvider:
    role: str
    provider: str
    model: str | None
    updated_at: float
    failure_class: str | None = None
    previous: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "updated_at": self.updated_at,
            "failure_class": self.failure_class,
            "previous": self.previous,
        }


def state_path(role: str = "rd-barb") -> Path:
    norm = role.strip().lower() or "rd-barb"
    if norm == "rd-barb":
        return rd_barb_state_dir() / STATE_FILE
    return rd_barb_state_dir() / f"{norm}-provider-active.json"


def read_active_provider(role: str = "rd-barb") -> ActiveProvider | None:
    path = state_path(role)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("rd-barb active provider unreadable (%s): %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    provider = str(raw.get("provider") or "").strip()
    if not provider:
        return None
    model = raw.get("model")
    return ActiveProvider(
        role=str(raw.get("role") or role),
        provider=provider,
        model=str(model) if model else None,
        updated_at=float(raw.get("updated_at") or 0.0),
        failure_class=str(raw["failure_class"]) if raw.get("failure_class") else None,
        previous=str(raw["previous"]) if raw.get("previous") else None,
    )


def record_active_provider(
    role: str,
    provider: str,
    *,
    model: str | None = None,
    failure_class: str | None = None,
    previous: str | None = None,
    now: float | None = None,
) -> ActiveProvider:
    """Write the active provider atomically; used by rd_barb_provider and the watchdog."""
    when = time.time() if now is None else now
    snap = ActiveProvider(
        role=role.strip().lower() or "rd-barb",
        provider=provider.strip().lower(),
        model=model,
        updated_at=when,
        failure_class=failure_class,
        previous=previous,
    )
    path = state_path(snap.role)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snap.to_dict()
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return snap


def active_provider_payload(role: str = "rd-barb") -> dict[str, Any]:
    snap = read_active_provider(role)
    return snap.to_dict() if snap else {"role": role, "provider": None, "model": None}
