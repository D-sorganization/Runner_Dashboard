"""The fleet agent roster, read from Repository_Management (issue #1244, #4587).

RM rejects a lease or presence for an agent outside
``shared_scripts.agent_identity.AGENT_IDS``; checking here first turns that
502 into a 422. The roster is read with one ``python -c`` in the RM checkout
(RM code is still never imported into the dashboard process) and cached until
the RM clone revision or ``agent_identity.py`` changes. ``runner-dashboard-rm-sync``
updates that checkout on a timer; this module rechecks at most every
``CHECK_INTERVAL`` seconds (mtime/HEAD fingerprint). A failed reload keeps the
last good roster (fail closed). When RM cannot answer and nothing is cached yet,
``STATIC_ROSTER`` is used and the read is retried after ``FALLBACK_SECONDS``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path

from coordination.rm_scripts import python_for_rm, run_argv
from staff.workspace import rm_root

log = logging.getLogger(__name__)

#: RM ``AGENT_IDS`` as of 2026-09-23 plus ``gemini`` / ``cursor-agent`` from RM PR #1707.
STATIC_ROSTER: tuple[str, ...] = (
    "user", "orchestrator", "maxwell-daemon", "claude", "codex", "antigravity", "conductor", "night-watch",
    "cartographer", "sanitation", "pr-remediator", "issue-remediator", "jules", "grok-pm", "local",
    "gemini", "cursor-agent", "grok", "qwen", "deepseek-api", "anthropic-api",
)  # fmt: skip
FALLBACK_SECONDS = 300.0
CHECK_INTERVAL = 60.0
_READ = "import json; from shared_scripts.agent_identity import AGENT_IDS; print(json.dumps(list(AGENT_IDS)))"

_clock = time.monotonic
_lock = threading.Lock()
_cached: tuple[str, ...] | None = None
_source_key: str = ""
_last_check = 0.0
_fallback_until = 0.0


def reset_cache() -> None:
    global _cached, _source_key, _last_check, _fallback_until
    with _lock:
        _cached, _source_key, _last_check, _fallback_until = None, "", 0.0, 0.0


def _git_head(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _roster_fingerprint(root: Path) -> str:
    """Fingerprint of the RM roster source: HEAD sha plus ``agent_identity.py`` identity."""
    module = root / "shared_scripts" / "agent_identity.py"
    if not module.is_file():
        return ""
    try:
        st = module.stat()
    except OSError:
        return ""
    head = _git_head(root)
    return f"{head}:{st.st_mtime_ns}:{st.st_size}"


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
    """RM's roster (cached, reloaded after rm-sync), else ``STATIC_ROSTER``. Post: non-empty."""
    global _cached, _source_key, _last_check, _fallback_until
    with _lock:
        now = _clock()
        if _cached is not None and now - _last_check < CHECK_INTERVAL:
            return _cached
        if _cached is not None:
            _last_check = now
            root = rm_root()
            if root is None:
                return _cached
            key = _roster_fingerprint(root)
            if key and key == _source_key:
                return _cached
            roster = _read_rm()
            if roster is not None:
                _cached = roster
                _source_key = key
                return roster
            log.warning("coordination roster reload failed; keeping last good roster (%d agents)", len(_cached))
            return _cached

        if now < _fallback_until:
            return STATIC_ROSTER
        _last_check = now
        root = rm_root()
        key = _roster_fingerprint(root) if root is not None else ""
        roster = _read_rm()
        if roster is None:
            _fallback_until = now + FALLBACK_SECONDS
            return STATIC_ROSTER
        _cached = roster
        _source_key = key
        return roster
