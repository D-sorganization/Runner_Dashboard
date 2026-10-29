"""Persist and read back the active rd-barb subscription CLI provider (#3527, #3549)."""

from __future__ import annotations

import datetime as _dt_mod
import json
import logging
import math
import os
import threading
import time
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

from staff.rd_barb_memory import rd_barb_state_dir  # noqa: E402

log = logging.getLogger("dashboard.staff.rd_barb_provider_state")

STATE_FILE = "provider-active.json"
DEFAULT_MAX_AGE_S = 3600.0
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


def _normalize_role(role: str = "rd-barb") -> str:
    norm = (role or "").strip().lower()
    return "rd-barb" if norm in ("", "barb", "rd-barb") else norm


def state_path(role: str = "rd-barb") -> Path:
    norm = _normalize_role(role)
    if norm == "rd-barb":
        return rd_barb_state_dir() / STATE_FILE
    return rd_barb_state_dir() / f"{norm}-provider-active.json"


def _effective_max_age(max_age_s: float | None) -> float | None:
    if max_age_s is not None:
        return max_age_s
    env_val = (os.environ.get("RD_BARB_PROVIDER_ACTIVE_MAX_AGE_S") or "").strip()
    if env_val:
        try:
            parsed = float(env_val)
            if math.isfinite(parsed) and parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_MAX_AGE_S


def read_active_provider(
    role: str = "rd-barb",
    *,
    max_age_s: float | None = None,
    now: float | None = None,
    allowed_providers: Collection[str] | None = None,
) -> ActiveProvider | None:
    """Read active provider state, returning ``None`` (with warning) if stale or corrupt (#3549)."""
    norm = _normalize_role(role)
    path = state_path(norm)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("rd-barb active provider unreadable (%s): %s; falling back to primary", path, exc)
        return None
    if not isinstance(raw, dict):
        log.warning("rd-barb active provider corrupt (%s): expected JSON object; falling back to primary", path)
        return None
    raw_provider = raw.get("provider")
    provider = str(raw_provider).strip().lower() if isinstance(raw_provider, str) else ""
    if not provider:
        log.warning("rd-barb active provider corrupt (%s): missing provider; falling back to primary", path)
        return None
    if allowed_providers is not None:
        allowed = {str(p).strip().lower() for p in allowed_providers if str(p).strip()}
        if allowed and provider not in allowed:
            log.warning(
                "rd-barb active provider corrupt (%s): provider %r not in allowed set; falling back to primary",
                path,
                provider,
            )
            return None
    raw_ts = raw.get("updated_at")
    if isinstance(raw_ts, bool) or raw_ts is None:
        log.warning("rd-barb active provider corrupt (%s): missing updated_at; falling back to primary", path)
        return None
    try:
        updated_at = float(raw_ts)
    except (TypeError, ValueError):
        log.warning(
            "rd-barb active provider corrupt (%s): invalid updated_at %r; falling back to primary",
            path,
            raw_ts,
        )
        return None
    if not math.isfinite(updated_at) or updated_at <= 0.0:
        log.warning(
            "rd-barb active provider corrupt (%s): non-positive updated_at %r; falling back to primary",
            path,
            raw_ts,
        )
        return None
    ttl = _effective_max_age(max_age_s)
    if ttl is not None:
        current_now = time.time() if now is None else now
        age = current_now - updated_at
        if age > ttl:
            log.warning(
                "rd-barb active provider state stale (%s, age=%.1fs > max_age=%.1fs); falling back to primary",
                path,
                age,
                ttl,
            )
            return None
    model = raw.get("model")
    return ActiveProvider(
        role=str(raw.get("role") or norm),
        provider=provider,
        model=str(model) if model else None,
        updated_at=updated_at,
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
        role=_normalize_role(role),
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


def active_provider_payload(
    role: str = "rd-barb",
    *,
    max_age_s: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    norm = _normalize_role(role)
    snap = read_active_provider(norm, max_age_s=max_age_s, now=now)
    return snap.to_dict() if snap else {"role": norm, "provider": None, "model": None}
