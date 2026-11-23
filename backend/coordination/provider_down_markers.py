"""Shared provider-down and rate-limited markers in RD state (#4564)."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coordination.lane_prompts import normalize_lane_provider

log = logging.getLogger("dashboard.coordination.provider_down_markers")

STATE_FILE = "provider-down-markers.json"
MARKER_KINDS = frozenset({"provider_down", "rate_limited"})

LANE_CHECKOUT_PROVIDER_CHAIN: tuple[str, ...] = (
    "codex",
    "claude",
    "agy",
    "cursor",
    "deepseek",
    "qwen",
)

_lock = threading.Lock()


@dataclass(frozen=True)
class ProviderMarker:
    provider: str
    kind: str
    until: float
    set_at: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "provider": self.provider,
            "kind": self.kind,
            "until": self.until,
            "set_at": self.set_at,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


def _state_dir() -> Path:
    env = (os.environ.get("RUNNER_DASHBOARD_STATE_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    configured = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    base = Path(configured).expanduser() if configured else Path("~/.config/runner-dashboard").expanduser()
    return base


def state_path() -> Path:
    return _state_dir() / STATE_FILE


def _parse_marker(raw: object) -> ProviderMarker | None:
    if not isinstance(raw, dict):
        return None
    provider = normalize_lane_provider(str(raw.get("provider") or ""))
    kind = str(raw.get("kind") or "").strip().lower()
    if not provider or kind not in MARKER_KINDS:
        return None
    try:
        until = float(raw["until"])
        set_at = float(raw.get("set_at") or until)
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(until) or not math.isfinite(set_at):
        return None
    reason = raw.get("reason")
    return ProviderMarker(
        provider=provider,
        kind=kind,
        until=until,
        set_at=set_at,
        reason=str(reason) if reason else None,
    )


def _load_raw() -> tuple[list[ProviderMarker], bool]:
    """Return markers and whether the store was readable (False => fail open)."""
    if "PYTEST_CURRENT_TEST" in os.environ and not (os.environ.get("RUNNER_DASHBOARD_STATE_DIR") or "").strip():
        return [], True
    path = state_path()
    if not path.is_file():
        return [], True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("provider-down markers unreadable (%s): %s; failing open", path, exc)
        return [], False
    if not isinstance(payload, dict):
        log.warning("provider-down markers corrupt (%s): expected object; failing open", path)
        return [], False
    items = payload.get("markers")
    if not isinstance(items, list):
        return [], True
    markers: list[ProviderMarker] = []
    for item in items:
        parsed = _parse_marker(item)
        if parsed is not None:
            markers.append(parsed)
    return markers, True


def _persist(markers: list[ProviderMarker]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "markers": [m.to_dict() for m in markers]}
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def list_active_markers(*, now: float | None = None) -> list[dict[str, Any]]:
    """Active markers only; expires stale entries and rewrites state when pruning."""
    current = time.time() if now is None else float(now)
    markers, _ok = _load_raw()
    active = [m for m in markers if m.until > current]
    if len(active) != len(markers):
        _persist(active)
    return [m.to_dict() for m in active]


def providers_marked_down(*, now: float | None = None) -> set[str]:
    current = time.time() if now is None else float(now)
    markers, store_ok = _load_raw()
    if not store_ok:
        return set()
    pruned = [m for m in markers if m.until > current]
    if len(pruned) != len(markers):
        _persist(pruned)
    return {m.provider for m in pruned}


def set_marker(
    provider: str,
    kind: str,
    *,
    until: float,
    now: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    norm = normalize_lane_provider(provider)
    if not norm:
        raise ValueError("provider required")
    kind_norm = kind.strip().lower()
    if kind_norm not in MARKER_KINDS:
        raise ValueError(f"invalid marker kind {kind!r}; expected one of {sorted(MARKER_KINDS)}")
    if not math.isfinite(until):
        raise ValueError("until must be a finite timestamp")
    ts = time.time() if now is None else float(now)
    marker = ProviderMarker(provider=norm, kind=kind_norm, until=float(until), set_at=ts, reason=reason)

    markers, _ = _load_raw()
    current = ts
    kept = [m for m in markers if m.until > current and not (m.provider == norm and m.kind == kind_norm)]
    kept.append(marker)
    _persist(kept)
    return marker.to_dict()


def clear_marker(provider: str, kind: str | None = None) -> int:
    norm = normalize_lane_provider(provider)
    if not norm:
        raise ValueError("provider required")
    kind_norm = kind.strip().lower() if kind else None
    if kind_norm is not None and kind_norm not in MARKER_KINDS:
        raise ValueError(f"invalid marker kind {kind!r}")

    markers, _ = _load_raw()
    before = len(markers)

    def _keep(m: ProviderMarker) -> bool:
        if m.provider != norm:
            return True
        if kind_norm is None:
            return False
        return m.kind != kind_norm

    kept = [m for m in markers if _keep(m)]
    if len(kept) != before:
        _persist(kept)
    return before - len(kept)


def resolve_lane_provider_with_failover(want_provider: str | None, *, now: float | None = None) -> str | None:
    """When the requested provider is marked down, walk the default lane chain; fail open with None."""
    down = providers_marked_down(now=now)
    if not down:
        return normalize_lane_provider(want_provider)

    chain = list(LANE_CHECKOUT_PROVIDER_CHAIN)
    norm_want = normalize_lane_provider(want_provider)

    if norm_want is None:
        for candidate in chain:
            if candidate not in down:
                return candidate
        return None

    if norm_want in chain:
        idx = chain.index(norm_want)
        order = chain[idx:] + chain[:idx]
    else:
        order = [norm_want, *[p for p in chain if p != norm_want]]

    for candidate in order:
        if candidate not in down:
            return candidate
    return None
