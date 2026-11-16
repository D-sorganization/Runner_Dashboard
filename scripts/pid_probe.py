"""Process liveness probe that is safe on Windows (#4421).

``os.kill(pid, 0)`` is a liveness probe only on POSIX. On Windows signal ``0``
is ``signal.CTRL_C_EVENT``: CPython calls ``GenerateConsoleCtrlEvent`` and the
CTRL+C reaches every process attached to the console, which has killed agent
CLI lanes and their launcher shells.

``pid_alive`` is the one place in this repository allowed to probe a pid.
``scripts/check_no_kill_zero.py`` fails on ``os.kill(x, 0)`` and
``os.kill(x, signal.CTRL_C_EVENT)`` anywhere else.
"""

from __future__ import annotations

import os
from typing import Any

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


def _kernel32() -> Any:
    """Return kernel32 with ``use_last_error`` set. Windows only."""
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]


def _last_error() -> int:
    import ctypes

    return int(ctypes.get_last_error())  # type: ignore[attr-defined,unused-ignore]


def _pid_alive_windows(pid: int) -> bool:
    """Query the process by handle. Sends nothing to the process or its console."""
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # The process exists but belongs to another user or is protected.
        return _last_error() == _ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        # The process exists; this user may not signal it.
        return True
    except OSError:
        return False
    return True


def pid_alive(pid: int) -> bool:
    """Return True when a process with this pid exists.

    Precondition: none; a pid that is not a positive ``int`` is never alive.
    Postcondition: no signal or console event is delivered to any process.
    A process this user may not query or signal counts as alive.
    On Windows a process that exited with code 259 reads as alive; that is the
    documented limit of ``GetExitCodeProcess``.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    return _pid_alive_posix(pid)
