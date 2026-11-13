"""Bound readiness waits without cancelling and duplicating thread work."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Iterable

Check = Callable[[], Awaitable[tuple[str, str | None]]]
Result = tuple[str, str | None]
_inflight: dict[str, asyncio.Task[Result]] = {}
log = logging.getLogger("dashboard.readiness")


async def _run(name: str, check: Check, budget: float) -> Result:
    previous = _inflight.get(name)
    if previous is not None and not previous.done():
        return "timed_out", None

    async def execute() -> Result:
        try:
            status, detail = await check()
            if status not in {"ok", "degraded", "down"}:
                raise ValueError("unknown readiness probe status")
            return status, detail
        except Exception as exc:  # noqa: BLE001
            log.warning("readyz probe %r raised: %s", name, type(exc).__name__)
            return "down", str(exc)

    task = asyncio.create_task(execute())
    _inflight[name] = task

    def finished(done: asyncio.Task[Result]) -> None:
        if _inflight.get(name) is done:
            del _inflight[name]

    task.add_done_callback(finished)
    try:
        # Cancelling to_thread's awaiter does not stop its worker. Retain the
        # task until actual completion, including after request cancellation.
        return await asyncio.wait_for(asyncio.shield(task), budget)
    except TimeoutError:
        return "timed_out", None


async def bounded_checks(
    checks: Iterable[tuple[str, Check]], component_budget: float, aggregate_budget: float
) -> list[tuple[str, Result]]:
    """Preserve completed results and retain one physical operation per name."""
    assert math.isfinite(component_budget) and component_budget > 0
    assert math.isfinite(aggregate_budget) and aggregate_budget > 0
    # A repeated name inside one request represents the same component.
    tasks = {name: asyncio.create_task(_run(name, check, component_budget)) for name, check in dict(checks).items()}
    if not tasks:
        return []
    try:
        await asyncio.wait(tasks.values(), timeout=aggregate_budget)
        return [(name, task.result() if task.done() else ("timed_out", None)) for name, task in tasks.items()]
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


async def subprocess_output(args: tuple[str, ...], timeout: float) -> bytes:
    """Own a child across spawn and communication, and reap on timeout."""
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    )
    proc = None
    try:
        async with asyncio.timeout(timeout):
            proc = await asyncio.shield(spawn)
            stdout, _ = await proc.communicate()
            return stdout
    except BaseException:
        # A spawn may finish after the budget. Keep this probe in flight until
        # that handle arrives and is reaped; never launch a replacement child.
        if proc is None:
            proc = await asyncio.shield(spawn)
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        raise
