"""The fleet agent roster, read from Repository_Management once (issue #1244).

RM rejects a lease or presence for an agent outside
``shared_scripts.agent_identity.AGENT_IDS``; checking here first turns that
502 into a 422. The roster is read with one ``python -c`` in the RM checkout
(RM code is still never imported into the dashboard process) and cached for
the process lifetime. When RM cannot answer, ``STATIC_ROSTER`` is used and the
read is retried after ``FALLBACK_SECONDS``.
"""

from __future__ import annotations

import json
import threading
import time

from coordination.rm_scripts import python_for_rm, run_argv
from staff.workspace import rm_root

#: RM ``AGENT_IDS`` as of 2026-09-23 plus ``gemini`` / ``cursor-agent`` from RM PR #1707.
STATIC_ROSTER: tuple[str, ...] = (
    "user", "orchestrator", "maxwell-daemon", "claude", "codex", "antigravity", "conductor", "night-watch",
    "cartographer", "sanitation", "pr-remediator", "issue-remediator", "jules", "grok-pm", "local", "gaai",
    "gemini", "cursor-agent",
)  # fmt: skip
FALLBACK_SECONDS = 300.0
_READ = "import json; from shared_scripts.agent_identity import AGENT_IDS; print(json.dumps(list(AGENT_IDS)))"

_lock = threading.Lock()
_cached: tuple[str, ...] | None = None
_fallback_until = 0.0


def reset_cache() -> None:
    global _cached, _fallback_until
    with _lock:
        _cached, _fallback_until = None, 0.0


def _read_rm() -> tuple[str, ...] | None:
    root = rm_root()
    if root is None:
        return None
    res = run_argv([python_for_rm(), "-c", _READ], cwd=root, timeout=30)
    try:
        data = json.loads(res.stdout.strip().splitlines()[-1]) if res.rc == 0 and res.stdout.strip() else None
    except ValueError:
        return None
    if not isinstance(data, list) or not data or not all(isinstance(a, str) for a in data):
        return None
    return tuple(data)


def agent_ids() -> tuple[str, ...]:
    """RM's roster (cached), else ``STATIC_ROSTER``. Post: non-empty."""
    global _cached, _fallback_until
    with _lock:
        if _cached is not None:
            return _cached
        if time.monotonic() < _fallback_until:
            return STATIC_ROSTER
        roster = _read_rm()
        if roster is None:
            _fallback_until = time.monotonic() + FALLBACK_SECONDS
            return STATIC_ROSTER
        _cached = roster
        return roster
