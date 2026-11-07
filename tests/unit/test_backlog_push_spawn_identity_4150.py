"""Regression tests for _verify_process_identity adversarial gaps (#4150).

#4147's _verify_process_identity treated an unreadable cmdline as a match and
accepted any substring match against any argument. Both let an unrelated or
reused PID be adopted as a recovered lane worker. (#4150)

The recorded pid is the ``claude`` child itself (backlog-push-lane.sh's
WORKER_PID), whose own argv never carries the lane entrypoint name or session
token — only its parent (the lane script invocation) does. Identity checks
must read the parent's cmdline, not the worker's own.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("bpush_spawn_4150", _ROOT / "scripts/barb-worker/backlog-push-spawn.py")
assert _spec and _spec.loader
spawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn)

_PID = 606
_PPID = 500


def _alive(pid: int):
    return patch("os.kill", side_effect=lambda p, sig: None if p == pid else (_ for _ in ()).throw(ProcessLookupError))


def _with_parent(ppid: int | None, parent_cmdline):
    """Patch ppid lookup and make cmdline lookup only resolve for that ppid.

    Querying the worker's own pid for cmdline must return None here, so a
    test only passes if the implementation actually consults the parent.
    """
    ppid_patch = patch.object(spawn, "_get_process_ppid", return_value=ppid)

    def _resolve(p: int):
        return parent_cmdline if (ppid is not None and p == ppid) else None

    cmdline_patch = patch.object(spawn, "_get_process_cmdline", side_effect=_resolve)
    return ppid_patch, cmdline_patch


def test_unreadable_parent_cmdline_rejects_adoption() -> None:
    """An unreadable parent /proc entry (cmdline is None) must not be treated as a match."""
    ppid_patch, cmdline_patch = _with_parent(_PPID, None)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "sup-reuse") is False


def test_missing_ppid_rejects_adoption() -> None:
    """An orphaned/unreadable parent pid lookup must not be treated as a match."""
    ppid_patch, cmdline_patch = _with_parent(None, None)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "sup-reuse") is False


def test_prefix_colliding_session_name_rejected() -> None:
    """A session that is only a prefix of another live session's token must not match."""
    cmdline = ["/bin/bash", "backlog-push-lane.sh", "lane-ab", "/tmp/x.log", "/tmp/x.prompt", "/tmp/x.pid"]
    ppid_patch, cmdline_patch = _with_parent(_PPID, cmdline)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "lane-a") is False


def test_unrelated_log_path_argument_rejected() -> None:
    """A session name appearing only inside an unrelated path argument must not match."""
    cmdline = ["/usr/bin/tail", "-f", "/tmp/lane-a.log"]
    ppid_patch, cmdline_patch = _with_parent(_PPID, cmdline)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "lane-a") is False


def test_exact_session_with_entrypoint_in_parent_matches() -> None:
    """The real lane script invocation (entrypoint + exact session token) must be adopted."""
    cmdline = ["/bin/bash", "/opt/Runner_Dashboard/scripts/barb-worker/backlog-push-lane.sh", "sup-reuse", "/tmp/x.log"]
    ppid_patch, cmdline_patch = _with_parent(_PPID, cmdline)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "sup-reuse") is True


def test_real_worker_cmdline_without_session_still_matches_via_parent() -> None:
    """The actual recorded pid is the `claude` child, whose own argv never has the
    session token or entrypoint — identity must still resolve through its parent."""
    worker_cmdline = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    parent_cmdline = ["/bin/bash", "backlog-push-lane.sh", "sup-reuse", "/tmp/x.log", "/tmp/x.prompt", "/tmp/x.pid"]
    by_pid = {_PID: worker_cmdline, _PPID: parent_cmdline}
    with (
        _alive(_PID),
        patch.object(spawn, "_get_process_ppid", return_value=_PPID),
        patch.object(spawn, "_get_process_cmdline", side_effect=by_pid.get),
    ):
        assert spawn._verify_process_identity(_PID, "sup-reuse") is True


def test_exact_session_without_entrypoint_rejected() -> None:
    """An exact session-name token on an unrelated command (no lane entrypoint) must not match."""
    cmdline = ["/usr/bin/some-other-tool", "--tag", "sup-reuse"]
    ppid_patch, cmdline_patch = _with_parent(_PPID, cmdline)
    with _alive(_PID), ppid_patch, cmdline_patch:
        assert spawn._verify_process_identity(_PID, "sup-reuse") is False


def test_dead_process_rejected_before_ppid_lookup() -> None:
    with patch("os.kill", side_effect=ProcessLookupError):
        assert spawn._verify_process_identity(_PID, "sup-reuse") is False
