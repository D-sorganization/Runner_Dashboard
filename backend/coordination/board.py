"""Presence and messages on the RM coordination board (issue #1229).

The board (Repository_Management issue #1576) stays the single message store;
this module only calls ``python -m scripts.agent_communicate``. Reads are
cached in-process for ``CACHE_SECONDS`` so one GitHub read serves every
caller: ``list --all-repos`` when RM supports it, else one ``list`` per fleet
repo (in parallel), merged. A board read takes seconds, so an expired entry is
served stale while one background thread refreshes it (stale-while-revalidate);
only the very first read, and the first read after a write, waits. Writes
invalidate the cache.

Read results: ``{available, complete, sessions, messages, conflicts, warnings}``
or ``{available: False, reason}``. Writes raise ``RMScriptError``.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from coordination.rm_scripts import ScriptResult, run_module
from staff.roles import load_roles

CACHE_SECONDS = 60.0
BOARD_REPO = "Repository_Management"
SCRIPT = "agent_communicate"
SESSION_KEYS = ("session", "agent", "repo", "issue", "branch", "paths", "goals", "expires", "at")
DEFAULT_GUIDANCE = "Coordination unavailable; retain leases and inspect active PRs before editing."

_clock = time.monotonic
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_refreshing: set[str] = set()
MAX_PARALLEL_READS = 4
_all_repos_supported: bool | None = None


class RMScriptError(RuntimeError):
    """An RM write script failed; carries its ``error`` and ``guidance`` for a 502."""

    def __init__(self, error: str, guidance: str = DEFAULT_GUIDANCE) -> None:
        super().__init__(error)
        self.error = error
        self.guidance = guidance

    def to_detail(self) -> dict[str, str]:
        return {"error": self.error, "guidance": self.guidance}


def reset_cache() -> None:
    """Forget cached reads and the ``--all-repos`` probe (tests, and after writes)."""
    global _all_repos_supported
    with _lock:
        _cache.clear()
        _refreshing.clear()
        _all_repos_supported = None


def _invalidate() -> None:
    with _lock:
        _cache.clear()


def _store(key: str, stamp: float, value: dict[str, Any]) -> None:
    if value.get("available"):
        with _lock:
            _cache[key] = (stamp, value)


def _refresh(key: str, loader: Any) -> None:
    try:
        _store(key, _clock(), loader())
    finally:
        with _lock:
            _refreshing.discard(key)


def _cached(key: str, loader: Any) -> dict[str, Any]:
    """Fresh hit → value; stale hit → value now + one background refresh; miss → load inline."""
    now = _clock()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        start_refresh = hit is not None and key not in _refreshing
        if start_refresh:
            _refreshing.add(key)
    if hit is not None:
        if start_refresh:
            threading.Thread(target=_refresh, args=(key, loader), name=f"coord-refresh-{key}", daemon=True).start()
        return hit[1]
    value = loader()
    _store(key, now, value)
    return value


def fleet_repos() -> list[str]:
    """Repos to poll when RM lacks ``--all-repos``: ``COORDINATION_REPOS`` or every role's repos, plus RM."""
    configured = os.environ.get("COORDINATION_REPOS", "")
    repos = {r.strip() for r in configured.split(",") if r.strip()}
    if not repos:
        for spec in load_roles().values():
            repos.update(spec.repos)
    repos.add(BOARD_REPO)
    return sorted(repos)


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "complete": False,
        "sessions": [],
        "messages": [],
        "conflicts": [],
        "warnings": [],
    }


def _parse_read(res: ScriptResult) -> dict[str, Any]:
    data = res.json()
    if data is None or data.get("error") or "sessions" not in data:
        return _unavailable(res.failure())
    return {
        "available": True,
        "complete": bool(data.get("complete", False)),
        "sessions": [_session(s) for s in data.get("sessions") or [] if isinstance(s, dict)],
        "messages": list(data.get("messages") or []),
        "conflicts": list(data.get("conflicts") or []),
        "warnings": [str(w) for w in data.get("warnings") or []],
    }


def _session(raw: dict[str, Any]) -> dict[str, Any]:
    return {**{k: raw.get(k) for k in SESSION_KEYS}, "source": "board"}


def _unsupported(res: ScriptResult) -> bool:
    return res.rc != 0 and "--all-repos" in res.stderr and "unrecognized" in res.stderr


def _read_all() -> dict[str, Any]:
    global _all_repos_supported
    if _all_repos_supported is not False:
        res = run_module(SCRIPT, "--repo", BOARD_REPO, "list", "--all-repos")
        if not _unsupported(res):
            parsed = _parse_read(res)
            _all_repos_supported = True if parsed["available"] else None
            return parsed
        _all_repos_supported = False
    repos = fleet_repos()
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_READS, max(len(repos), 1))) as pool:
        parts = list(pool.map(lambda repo: _parse_read(run_module(SCRIPT, "--repo", repo, "list")), repos))
    return _merge(parts)


def _merge(parts: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [p for p in parts if p["available"]]
    if not ok:
        return parts[0] if parts else _unavailable("no fleet repositories configured")
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for part in ok:
        for s in part["sessions"]:
            seen.setdefault((str(s.get("session")), str(s.get("repo"))), s)
    warnings = [w for p in ok for w in p["warnings"]]
    warnings += [f"board read failed: {p['reason']}" for p in parts if not p["available"]]
    return {
        "available": True,
        "complete": all(p["complete"] for p in parts),
        "sessions": list(seen.values()),
        "messages": [],
        "conflicts": [],
        "warnings": list(dict.fromkeys(warnings)),
    }


def read_sessions(repo: str | None = None) -> dict[str, Any]:
    """Every live board session (optionally only ``repo``, case-insensitive)."""
    data = _cached("all", _read_all)
    if not data["available"] or not repo:
        return data
    wanted = repo.casefold()
    return {**data, "sessions": [s for s in data["sessions"] if str(s.get("repo") or "").casefold() == wanted]}


def read_inbox(session: str, repo: str) -> dict[str, Any]:
    """Messages and path/goal conflicts addressed to ``session``."""

    def load() -> dict[str, Any]:
        return _parse_read(run_module(SCRIPT, "--repo", repo, "--session", session, "inbox"))

    data = _cached(f"inbox:{repo.casefold()}:{session}", load)
    keys = ("available", "reason", "complete", "messages", "conflicts", "warnings")
    return {k: data[k] for k in keys if k in data}


def publish(repo: str, session: str, command: str, *args: str) -> dict[str, Any]:
    """Run one board write (``register``/``release``/``send``/``ack``). Post: cache cleared."""
    res = run_module(SCRIPT, "--repo", repo, "--session", session, command, *args)
    _invalidate()
    data = res.json()
    if data is None or not data.get("ok"):
        detail = data or {}
        raise RMScriptError(res.failure(), str(detail.get("guidance") or DEFAULT_GUIDANCE))
    return data
