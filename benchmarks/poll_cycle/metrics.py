"""Measurement helpers: event-loop stall monitor, subprocess counter, result table."""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOOP_TICK_S = 0.005
MS_PER_S = 1000


class LagMonitor:
    """Records the worst overshoot of ``asyncio.sleep(LOOP_TICK_S)`` (a loop-stall proxy)."""

    def __init__(self) -> None:
        self.max_lag_s = 0.0
        self._stopped = False
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while not self._stopped:
            started = time.perf_counter()
            await asyncio.sleep(LOOP_TICK_S)
            overshoot = time.perf_counter() - started - LOOP_TICK_S
            self.max_lag_s = max(self.max_lag_s, overshoot)

    def start(self) -> None:
        """Start monitoring on the running loop."""
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop monitoring and wait for the task to finish."""
        self._stopped = True
        if self._task is not None:
            await self._task


class SpawnCounter:
    """Counts ``subprocess.Popen`` constructions by executable name while installed."""

    def __init__(self, popen_cls: Any = subprocess.Popen) -> None:
        # Bound at import time so a later swap of ``subprocess.Popen`` (e.g. a test guard) is harmless.
        self.popen_cls = popen_cls
        self.by_name: Counter[str] = Counter()

    @property
    def gh(self) -> int:
        """Number of ``gh`` spawns."""
        return self.by_name["gh"]

    @property
    def other(self) -> int:
        """Number of non-``gh`` spawns."""
        return sum(self.by_name.values()) - self.gh

    @contextlib.contextmanager
    def installed(self) -> Iterator[SpawnCounter]:
        """Patch ``popen_cls.__init__`` for the duration of the block."""
        original = self.popen_cls.__init__
        counter = self

        def counting_init(popen: Any, args: Any, *a: Any, **kw: Any) -> None:
            counter.by_name[Path(str(args[0] if isinstance(args, (list, tuple)) else args).split()[0]).name] += 1
            original(popen, args, *a, **kw)

        self.popen_cls.__init__ = counting_init
        try:
            yield self
        finally:
            self.popen_cls.__init__ = original


@dataclass
class PhaseResult:
    """Measurements for one request (cold or warm)."""

    status: int | str
    ms: int
    github_http: int
    gh_cli: int
    other_spawns: int
    max_stall_ms: int


@dataclass
class EndpointResult:
    """Cold and warm measurements for one endpoint."""

    endpoint: str
    cold: PhaseResult
    warm: PhaseResult


_COLUMNS: tuple[tuple[str, Callable[[EndpointResult], object]], ...] = (
    ("endpoint", lambda r: r.endpoint),
    ("cold ms", lambda r: r.cold.ms),
    ("http", lambda r: r.cold.github_http),
    ("gh", lambda r: r.cold.gh_cli),
    ("other", lambda r: r.cold.other_spawns),
    ("stall ms", lambda r: r.cold.max_stall_ms),
    ("status", lambda r: r.cold.status),
    ("warm ms", lambda r: r.warm.ms),
    ("warm calls", lambda r: r.warm.github_http + r.warm.gh_cli),
    ("warm stall", lambda r: r.warm.max_stall_ms),
)


def format_table(results: Sequence[EndpointResult]) -> str:
    """Render results as a fixed-width text table (first column left-aligned)."""
    rows = [[name for name, _ in _COLUMNS]] + [[str(get(r)) for _, get in _COLUMNS] for r in results]
    widths = [max(len(cell) for cell in column) for column in zip(*rows, strict=True)]
    return "\n".join(
        "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths, strict=True)))
        for row in rows
    )
