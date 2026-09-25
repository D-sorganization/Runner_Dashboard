"""Run a coroutine on the event loop from a synchronous staff action executor.

Action executors are synchronous; the proposal routes run them in an anyio worker
thread (``anyio.to_thread.run_sync``) so they never block the loop. Loop-bound work
(the ``gh_client`` httpx client, the shared dispatch service) is sent back to the
loop with :func:`run_on_loop`. Called from anywhere else the bridge is unavailable
and :class:`BridgeUnavailableError` is raised instead of deadlocking or hanging.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import anyio.from_thread

T = TypeVar("T")


class BridgeUnavailableError(RuntimeError):
    """The caller is not in an anyio worker thread, so the event loop is unreachable."""


def _noop() -> None:
    return None


def run_on_loop(fn: Callable[..., Coroutine[Any, Any, T]], *args: Any) -> T:
    """Await ``fn(*args)`` on the event loop that owns this worker thread.

    Pre: called from an anyio worker thread. Post: returns ``fn``'s result or re-raises
    its exception; outside a worker thread raises :class:`BridgeUnavailableError`
    without calling ``fn``.
    """
    try:
        anyio.from_thread.run_sync(_noop)
    except RuntimeError as exc:
        raise BridgeUnavailableError(f"needs an anyio worker thread: {exc}") from exc
    return anyio.from_thread.run(fn, *args)
