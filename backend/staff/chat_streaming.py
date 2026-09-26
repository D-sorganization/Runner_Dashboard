"""Live process stream reader for staff chat turns (SC-B1-G10, Issue #1493).

Reads subprocess stdout incrementally via a background worker thread, yielding lines
asynchronously to the caller so token SSE events can be published live while
the provider CLI process is still executing.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from staff.adapters import ProviderAdapter
from staff.chat_history import extract_session_id

log = logging.getLogger("dashboard.staff.chat_streaming")


def spawn_cli_process(
    cmd: list[str],
    cwd: str,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    """Spawn the provider CLI subprocess in read-only scratch mode."""
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


class LiveProcessReader:
    """Incrementally streams lines from a subprocess stdout while capturing stderr and returncode."""

    def __init__(self, proc: Any) -> None:
        self.proc = proc
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.returncode: int = 0
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._stderr_thread: threading.Thread | None = None
        self._stdout_thread: threading.Thread | None = None

    def _read_stdout_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            stdout = getattr(self.proc, "stdout", None)
            if stdout is not None:
                for line in stdout:
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    loop.call_soon_threadsafe(self._queue.put_nowait, ("line", line))
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(self._queue.put_nowait, ("err", exc))
        finally:
            loop.call_soon_threadsafe(self._queue.put_nowait, ("eof", None))

    def _read_stderr_worker(self) -> None:
        err_lines: list[str] = []
        try:
            stderr = getattr(self.proc, "stderr", None)
            if stderr is not None:
                for eline in stderr:
                    if isinstance(eline, bytes):
                        eline = eline.decode("utf-8", errors="replace")
                    err_lines.append(eline)
        except Exception as exc:  # noqa: BLE001
            log.debug("Error reading stderr in worker: %s", exc)
        self.stderr_lines = err_lines

    async def stream_lines(self) -> AsyncIterator[str]:
        """Yield lines from stdout as they arrive until EOF, then await completion."""
        loop = asyncio.get_running_loop()

        self._stderr_thread = threading.Thread(target=self._read_stderr_worker, daemon=True)
        self._stderr_thread.start()

        self._stdout_thread = threading.Thread(
            target=self._read_stdout_worker,
            args=(loop,),
            daemon=True,
        )
        self._stdout_thread.start()

        try:
            while True:
                kind, val = await self._queue.get()
                if kind == "line":
                    self.stdout_lines.append(val)
                    yield val
                elif kind == "eof":
                    break
                elif kind == "err":
                    log.warning("Live process stdout read error: %s", val)
                    break
        finally:
            if callable(getattr(self.proc, "poll", None)):
                if self.proc.poll() is None and callable(getattr(self.proc, "kill", None)):
                    try:
                        self.proc.kill()
                    except Exception:  # noqa: BLE001
                        pass

        if self._stderr_thread and self._stderr_thread.is_alive():
            await loop.run_in_executor(None, self._stderr_thread.join)

        rc: Any = None
        if callable(getattr(self.proc, "wait", None)):
            try:
                rc = await loop.run_in_executor(None, self.proc.wait)
            except Exception:  # noqa: BLE001
                rc = None
        if not isinstance(rc, int):
            rc = getattr(self.proc, "returncode", None)
        if not isinstance(rc, int):
            rc = 0
        self.returncode = rc


@dataclass
class TurnStreamOutput:
    """Captured streams and timing metrics from live turn execution."""

    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    returncode: int = 0
    session_id: str | None = None
    t_first_token: float | None = None
    deltas: list[str] = field(default_factory=list)
    # Full text of the terminal ``result`` event, which repeats everything already streamed.
    result_text: str = ""
    json_lines: bool = False

    @property
    def reply_text(self) -> str:
        """The reply exactly once (#1341).

        Post: a JSON-lines stream yields its ``result`` text when it has one,
        else its streamed deltas; a plain-text stream yields stdout verbatim, so
        line breaks and blank lines survive.
        """
        if not self.json_lines:
            return "".join(self.stdout_lines)
        return self.result_text or "".join(self.deltas)


async def stream_turn_output(
    reader: LiveProcessReader,
    adapter: ProviderAdapter,
    bus: Any,
    thread_id: str,
    placeholder_id: str,
) -> TurnStreamOutput:
    """Streams stdout lines incrementally, publishes tokens live over SSE bus, and returns final output."""
    stdout_text: list[str] = []
    deltas: list[str] = []
    t_first_token: float | None = None
    captured_session_id: str | None = None
    result_text = ""

    async for line in reader.stream_lines():
        stdout_text.append(line)
        event = adapter.parse_line(line)

        detected_sid = extract_session_id(adapter.provider_id, event, raw_line=line)
        if detected_sid:
            captured_session_id = detected_sid

        delta = event.get("text", "")
        if adapter.json_lines and event.get("kind") == "result":
            # The result event repeats the whole reply; stream it only when nothing was streamed before.
            result_text = delta
            if deltas:
                continue
        if delta:
            deltas.append(delta)
            if t_first_token is None:
                t_first_token = time.monotonic()
            await bus.publish_token(thread_id, placeholder_id, delta)

    return TurnStreamOutput(
        stdout_lines=stdout_text,
        stderr_lines=reader.stderr_lines,
        returncode=reader.returncode,
        session_id=captured_session_id,
        t_first_token=t_first_token,
        deltas=deltas,
        result_text=result_text,
        json_lines=adapter.json_lines,
    )
