"""Live subscription quota for staff providers (issue #1587).

Every agent CLI the staff use is billed through a flat subscription, so the
limit that actually runs out is each plan's rolling usage window, not dollars.
Two CLIs publish those windows locally at no cost:

* **Claude Code** emits a ``rate_limit_event`` in ``-p --output-format
  stream-json`` output (``rate_limit_info.unifiedWindows.<window>.utilization``,
  a 0–1 fraction) and hands ``rate_limits.<window>.used_percentage`` to its
  status-line command in interactive sessions.
* **Codex** writes ``token_count`` events with ``rate_limits.primary|secondary``
  (``used_percent``, ``window_minutes``, ``resets_at``) to its session logs
  under ``~/.codex/sessions/YYYY/MM/DD/*.jsonl``.

Gemini, Antigravity and Cursor expose no quota query, so their windows are
unknown. This module parses those sources into :class:`QuotaSnapshot` values,
keeps the newest snapshot per plan account in a small JSON store next to the
scheduler state, and answers "how full is this provider's plan right now".
It never calls a CLI and never costs quota.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.quota")

STATE_FILE = "staff_quota.json"
# How each provider is paid for. "subscription" plans have usage windows;
# "local" models cost nothing; anything else is "unknown".
BILLING: dict[str, str] = {
    "claude": "subscription",
    "codex": "subscription",
    "antigravity": "subscription",
    "gemini": "subscription",
    "cursor-agent": "subscription",
    "ollama": "local",
    "claude-ollama": "local",
    "maxwell": "local",
}
# Provider → the plan account whose windows gate it, for providers whose quota is
# readable. ``ollama`` runs Codex with ``--oss`` on local models, not the ChatGPT
# plan, and ``claude-ollama`` talks to Ollama, not Anthropic.
QUOTA_ACCOUNT: dict[str, str] = {"claude": "claude", "codex": "codex"}
_WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}
_CODEX_FILES_SCANNED = 5
_CODEX_TAIL_BYTES = 512 * 1024


def _utc(ts: Any) -> datetime | None:
    """Epoch seconds or ISO-8601 → aware UTC datetime; anything else → None."""
    if isinstance(ts, bool):
        return None
    if isinstance(ts, int | float):
        return datetime.fromtimestamp(float(ts), UTC)
    if isinstance(ts, str) and ts.strip():
        try:
            parsed = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _iso(when: datetime | None) -> str | None:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z") if when else None


def _percent(value: Any, *, fraction: bool) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    pct = float(value) * 100.0 if fraction else float(value)
    return max(0.0, min(100.0, pct))


@dataclass(frozen=True)
class QuotaWindow:
    """One rolling usage window of a plan (e.g. ``five_hour`` at 23 %)."""

    name: str
    used_percent: float
    resets_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "used_percent": round(self.used_percent, 1), "resets_at": _iso(self.resets_at)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuotaWindow:
        return cls(str(data["name"]), float(data["used_percent"]), _utc(data.get("resets_at")))


@dataclass(frozen=True)
class QuotaSnapshot:
    """What one source said about one plan account at one moment."""

    account: str
    observed_at: datetime
    source: str
    windows: tuple[QuotaWindow, ...]
    plan: str | None = None
    # Set while the provider reports it is refusing requests (hard limit hit).
    limited_until: datetime | None = None

    def current(self, now: datetime) -> QuotaSnapshot:
        """The snapshot as of ``now``: windows past their reset time are dropped.

        Post: every remaining window resets after ``now`` (or has no known
        reset); ``limited_until`` is cleared once it has passed.
        """
        windows = tuple(w for w in self.windows if w.resets_at is None or w.resets_at > now)
        limited = self.limited_until if self.limited_until and self.limited_until > now else None
        return replace(self, windows=windows, limited_until=limited)

    def peak(self) -> QuotaWindow | None:
        """The fullest window, or None when no window is known."""
        if not self.windows:
            return None
        return max(self.windows, key=lambda w: w.used_percent)

    def to_dict(self) -> dict[str, Any]:
        peak = self.peak()
        return {
            "account": self.account,
            "observed_at": _iso(self.observed_at),
            "source": self.source,
            "plan": self.plan,
            "limited_until": _iso(self.limited_until),
            "peak_percent": round(peak.used_percent, 1) if peak else None,
            "windows": [w.to_dict() for w in self.windows],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuotaSnapshot:
        observed = _utc(data.get("observed_at"))
        if observed is None:
            raise ValueError("snapshot without observed_at")
        return cls(
            account=str(data["account"]),
            observed_at=observed,
            source=str(data.get("source") or ""),
            windows=tuple(QuotaWindow.from_dict(w) for w in data.get("windows") or ()),
            plan=data.get("plan"),
            limited_until=_utc(data.get("limited_until")),
        )


# ── parsers ──────────────────────────────────────────────────────────────
def from_claude_event(raw: Mapping[str, Any], *, observed_at: datetime) -> QuotaSnapshot | None:
    """Parse a Claude Code stream-json ``rate_limit_event``; None for any other event."""
    if raw.get("type") != "rate_limit_event":
        return None
    info = raw.get("rate_limit_info")
    if not isinstance(info, Mapping):
        return None
    windows: list[QuotaWindow] = []
    unified = info.get("unifiedWindows")
    if isinstance(unified, Mapping):
        for name, window in unified.items():
            if isinstance(window, Mapping):
                pct = _percent(window.get("utilization"), fraction=True)
                if pct is not None:
                    windows.append(QuotaWindow(str(name), pct, _utc(window.get("resetsAt"))))
    if not windows:
        pct = _percent(info.get("utilization"), fraction=True)
        if pct is not None and info.get("rateLimitType"):
            windows.append(QuotaWindow(str(info["rateLimitType"]), pct, _utc(info.get("resetsAt"))))
    if not windows:
        return None
    limited = _utc(info.get("resetsAt")) if info.get("status") == "rejected" else None
    return QuotaSnapshot("claude", observed_at, "claude-stream", tuple(windows), limited_until=limited)


def from_claude_statusline(payload: Mapping[str, Any], *, observed_at: datetime) -> QuotaSnapshot | None:
    """Parse the ``rate_limits`` block Claude Code hands its status-line command."""
    limits = payload.get("rate_limits")
    if not isinstance(limits, Mapping):
        return None
    windows = [
        QuotaWindow(str(name), pct, _utc(window.get("resets_at")))
        for name, window in limits.items()
        if isinstance(window, Mapping) and (pct := _percent(window.get("used_percentage"), fraction=False)) is not None
    ]
    if not windows:
        return None
    return QuotaSnapshot("claude", observed_at, "claude-statusline", tuple(windows))


def from_codex_line(obj: Mapping[str, Any]) -> QuotaSnapshot | None:
    """Parse one Codex session-log line; None unless it carries ``rate_limits``."""
    payload = obj.get("payload")
    if not isinstance(payload, Mapping):
        return None
    limits = payload.get("rate_limits")
    observed = _utc(obj.get("timestamp"))
    if not isinstance(limits, Mapping) or observed is None:
        return None
    windows: dict[str, QuotaWindow] = {}
    for slot in ("primary", "secondary"):
        window = limits.get(slot)
        if not isinstance(window, Mapping):
            continue
        pct = _percent(window.get("used_percent"), fraction=False)
        if pct is None:
            continue
        minutes = window.get("window_minutes")
        name = _WINDOW_NAMES.get(minutes, f"{minutes}_minute") if isinstance(minutes, int) else slot
        windows[slot] = QuotaWindow(name, pct, _utc(window.get("resets_at")))
    if not windows:
        return None
    reached = limits.get("rate_limit_reached_type")
    limited = None
    if reached:
        hit = windows.get(str(reached)) or max(windows.values(), key=lambda w: w.used_percent)
        limited = hit.resets_at
    plan = limits.get("plan_type")
    return QuotaSnapshot(
        "codex",
        observed,
        "codex-session-log",
        tuple(windows.values()),
        plan=str(plan) if plan else None,
        limited_until=limited,
    )


# ── codex session logs ───────────────────────────────────────────────────
def codex_session_dirs() -> list[Path]:
    """Session-log roots: ``STAFF_CODEX_SESSION_DIRS`` (os.pathsep list), else ``$CODEX_HOME/sessions``."""
    configured = os.environ.get("STAFF_CODEX_SESSION_DIRS", "").strip()
    if configured:
        return [Path(part).expanduser() for part in configured.split(os.pathsep) if part.strip()]
    home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    return [home / "sessions"]


def _newest_session_files(dirs: Iterable[Path], limit: int) -> list[Path]:
    files: list[tuple[float, Path]] = []
    for root in dirs:
        if not root.is_dir():
            continue
        try:
            for path in root.glob("*/*/*/*.jsonl"):
                try:
                    files.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        except OSError:
            continue
    files.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in files[:limit]]


def _last_snapshot_in(path: Path) -> QuotaSnapshot | None:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _CODEX_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"rate_limits"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, Mapping) and (snap := from_codex_line(obj)) is not None:
            return snap
    return None


def read_codex_sessions(dirs: Sequence[Path] | None = None) -> QuotaSnapshot | None:
    """Newest Codex quota snapshot found in the most recent session logs, or None."""
    found = [
        snap
        for path in _newest_session_files(dirs if dirs is not None else codex_session_dirs(), _CODEX_FILES_SCANNED)
        if (snap := _last_snapshot_in(path)) is not None
    ]
    if not found:
        return None
    return max(found, key=lambda s: s.observed_at)


# ── store ────────────────────────────────────────────────────────────────
def state_path() -> Path:
    return Path(os.environ.get("STAFF_QUOTA_STATE", str(_config_dir() / STATE_FILE))).expanduser()


class QuotaStore:
    """Newest snapshot per plan account, persisted as JSON (atomic replace)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path or state_path()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("staff quota: state file %s unreadable (%s); starting fresh", self.path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, account: str) -> QuotaSnapshot | None:
        with self._lock:
            raw = self._load().get(account)
        if not isinstance(raw, Mapping):
            return None
        try:
            return QuotaSnapshot.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None

    def record(self, snapshot: QuotaSnapshot) -> bool:
        """Keep ``snapshot`` unless a newer one is already stored. Returns whether it was kept."""
        with self._lock:
            data = self._load()
            existing = data.get(snapshot.account)
            if isinstance(existing, Mapping):
                seen = _utc(existing.get("observed_at"))
                if seen is not None and seen >= snapshot.observed_at:
                    return False
            data[snapshot.account] = snapshot.to_dict()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
            return True


_STORE = QuotaStore()


def default_store() -> QuotaStore:
    return _STORE


# ── per-provider view ────────────────────────────────────────────────────
def billing(provider: str) -> str:
    return BILLING.get(provider, "unknown")


def observe(
    provider: str,
    raw: Mapping[str, Any],
    *,
    store: QuotaStore | None = None,
    now: datetime | None = None,
) -> bool:
    """Record a quota event seen in a staff run's output. Returns whether it was stored."""
    if QUOTA_ACCOUNT.get(provider) != "claude":
        return False
    snap = from_claude_event(raw, observed_at=now or datetime.now(UTC))
    return snap is not None and (store or _STORE).record(snap)


def for_provider(
    provider: str,
    now: datetime,
    *,
    store: QuotaStore | None = None,
    codex_dirs: Sequence[Path] | None = None,
) -> QuotaSnapshot | None:
    """Newest known quota for ``provider``'s plan as of ``now``; None when unreadable or unseen."""
    account = QUOTA_ACCOUNT.get(provider)
    if account is None:
        return None
    candidates = [(store or _STORE).get(account)]
    if account == "codex":
        candidates.append(read_codex_sessions(codex_dirs))
    newest = max((c for c in candidates if c is not None), key=lambda s: s.observed_at, default=None)
    return newest.current(now) if newest else None


def report(
    providers: Iterable[str],
    now: datetime,
    *,
    store: QuotaStore | None = None,
    codex_dirs: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Body of ``GET /api/staff/quota``: billing kind and current windows per provider."""
    rows = []
    for provider in providers:
        snap = for_provider(provider, now, store=store, codex_dirs=codex_dirs)
        rows.append(
            {
                "provider": provider,
                "billing": billing(provider),
                "readable": provider in QUOTA_ACCOUNT,
                "quota": snap.to_dict() if snap else None,
            }
        )
    return {"generated_at": _iso(now), "providers": rows}
