"""Regression tests for _verify_process_identity adversarial gaps (#4150).

#4147's _verify_process_identity treated an unreadable cmdline as a match and
accepted any substring match against any argument. Both let an unrelated or
reused PID be adopted as a recovered lane worker. (#4150)
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


def _alive(pid: int):
    return patch("os.kill", side_effect=lambda p, sig: None if p == pid else (_ for _ in ()).throw(ProcessLookupError))


def test_unreadable_cmdline_rejects_adoption() -> None:
    """An unreadable /proc entry (cmdline is None) must not be treated as a match."""
    with _alive(606), patch.object(spawn, "_get_process_cmdline", return_value=None):
        assert spawn._verify_process_identity(606, "sup-reuse") is False


def test_prefix_colliding_session_name_rejected() -> None:
    """A session that is only a prefix of another live session's token must not match."""
    cmdline = ["/bin/bash", "backlog-push-lane.sh", "lane-ab", "/tmp/x.log", "/tmp/x.prompt", "/tmp/x.pid"]
    with _alive(606), patch.object(spawn, "_get_process_cmdline", return_value=cmdline):
        assert spawn._verify_process_identity(606, "lane-a") is False


def test_unrelated_log_path_argument_rejected() -> None:
    """A session name appearing only inside an unrelated path argument must not match."""
    cmdline = ["/usr/bin/tail", "-f", "/tmp/lane-a.log"]
    with _alive(606), patch.object(spawn, "_get_process_cmdline", return_value=cmdline):
        assert spawn._verify_process_identity(606, "lane-a") is False


def test_exact_session_with_entrypoint_still_matches() -> None:
    """The real worker command (entrypoint + exact session token) must still be adopted."""
    cmdline = ["/bin/bash", "/opt/Runner_Dashboard/scripts/barb-worker/backlog-push-lane.sh", "sup-reuse", "/tmp/x.log"]
    with _alive(606), patch.object(spawn, "_get_process_cmdline", return_value=cmdline):
        assert spawn._verify_process_identity(606, "sup-reuse") is True


def test_exact_session_without_entrypoint_rejected() -> None:
    """An exact session-name token on an unrelated command (no lane entrypoint) must not match."""
    cmdline = ["/usr/bin/some-other-tool", "--tag", "sup-reuse"]
    with _alive(606), patch.object(spawn, "_get_process_cmdline", return_value=cmdline):
        assert spawn._verify_process_identity(606, "sup-reuse") is False


def test_dead_process_rejected_before_cmdline_check() -> None:
    with patch("os.kill", side_effect=ProcessLookupError):
        assert spawn._verify_process_identity(606, "sup-reuse") is False
