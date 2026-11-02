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
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from staff.adapters import ProviderAdapter
from staff.chat_history import extract_session_id

log = logging.getLogger("dashboard.staff.chat_streaming")


class _SummaryCapsuleStreamFilter:
    """Hide the opt-in final summary capsule from live tokens without changing capture."""

    _MARKER = "```staff-memory"

    def __init__(self) -> None:
        self.hidden = False
        self._fence: str | None = None
        self._line_start = True
        self._candidate = ""
        self._candidate_cr = False
        self._line_prefix = ""
        self._line_length = 0
        self._line_trailing_cr = False

    def feed(self, text: str) -> str:
        """Return publishable bytes, holding only a possible marker prefix."""
        if self.hidden:
            return ""
        output: list[str] = []
        for char in text:
            if self.hidden:
                continue
            if char == "\n":
                if self._candidate == self._MARKER:
                    self.hidden = True
                    self._candidate = ""
                    self._candidate_cr = False
                elif self._candidate:
                    output.append(self._candidate)
                    if self._candidate_cr:
                        output.append("\r")
                    self._candidate = ""
                    self._candidate_cr = False
                    output.append(char)
                else:
                    output.append(char)
                self._finish_line()
                continue

            self._line_length = min(self._line_length + 1, 4)
            self._line_trailing_cr = char == "\r"
            if len(self._line_prefix) < 3:
                self._line_prefix += char

            if self._candidate_cr:
                # CRLF is also an exact marker line; other continuations invalidate it.
                output.append(self._candidate)
                output.append("\r")
                self._candidate = ""
                self._candidate_cr = False
                output.append(char)
                self._line_start = False
                continue

            if self._candidate == self._MARKER:
                if char == "\r":
                    self._candidate_cr = True
                else:
                    output.append(self._candidate)
                    self._candidate = ""
                    output.append(char)
                self._line_start = False
                continue

            if self._candidate:
                proposed = self._candidate + char
                if self._MARKER.startswith(proposed):
                    self._candidate = proposed
                else:
                    output.append(self._candidate)
                    self._candidate = ""
                    output.append(char)
                self._line_start = False
                continue

            if self._line_start and self._fence is None and self._MARKER.startswith(char):
                self._candidate = char
            else:
                output.append(char)
            self._line_start = False
        return "".join(output)

    def finish(self) -> str:
        """Flush an incomplete marker prefix; it is not a recognized capsule."""
        if self.hidden or not self._candidate:
            return ""
        suffix = "\r" if self._candidate_cr else ""
        pending = self._candidate + suffix
        self._candidate = ""
        self._candidate_cr = False
        return pending

    def _finish_line(self) -> None:
        line_length = self._line_length - int(self._line_trailing_cr)
        if self._fence is None:
            if line_length >= 3 and self._line_prefix in {"```", "~~~"}:
                self._fence = self._line_prefix
        elif line_length == 3 and self._line_prefix == self._fence:
            self._fence = None
        self._line_start = True
        self._line_prefix = ""
        self._line_length = 0
        self._line_trailing_cr = False


def spawn_cli_process(
    cmd: list[str],
    cwd: str,
    env: dict[str, str],
    stdin_text: str | None = None,
) -> subprocess.Popen[str]:
    """Spawn the provider CLI subprocess in read-only scratch mode.

    When ``stdin_text`` is given it is written to the child's stdin and stdin is closed, so a prompt too
    long for argv (Windows command-line limit under WSL interop, #2744) still reaches the CLI.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
        except OSError:  # the CLI exited early; its exit code and stderr carry the failure
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            # Popen.communicate() re-flushes self.stdin if it is still set, which raises on an
            # already-closed file; clear the attribute now that stdin has been fully delivered.
            proc.stdin = None
    return proc


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
    hide_memory_summary: bool = False,
    epoch: int | None = None,
    is_current_epoch: Callable[[], bool] | None = None,
) -> TurnStreamOutput:
    """Streams stdout lines incrementally, publishes tokens live over SSE bus, and returns final output."""
    stdout_text: list[str] = []
    deltas: list[str] = []
    t_first_token: float | None = None
    captured_session_id: str | None = None
    result_text = ""
    chat_json = getattr(adapter, "chat_json", adapter.json_lines)
    summary_filter = _SummaryCapsuleStreamFilter() if hide_memory_summary else None

    async for line in reader.stream_lines():
        stdout_text.append(line)
        event = adapter.parse_line(line)

        detected_sid = extract_session_id(adapter.provider_id, event, raw_line=line)
        if detected_sid and (not captured_session_id or adapter.provider_id not in ("codex", "ollama")):
            captured_session_id = detected_sid

        # Plain-text parse_line strips newlines (and can interpret JSON prose as
        # events). The capsule filter needs the same exact text as final capture.
        preserve_line = adapter.provider_id in ("codex", "ollama") and event.get("kind") in ("text", "json")
        delta = line if preserve_line or (hide_memory_summary and not chat_json) else event.get("text", "")
        if chat_json and event.get("kind") == "result":
            # The result event repeats the whole reply; stream it only when nothing was streamed before.
            result_text = delta
            if deltas:
                continue
        if delta:
            deltas.append(delta)
            if t_first_token is None:
                t_first_token = time.monotonic()
            publishable = summary_filter.feed(delta) if summary_filter is not None else delta
            if publishable and (is_current_epoch is None or is_current_epoch()):
                if epoch is None:
                    await bus.publish_token(thread_id, placeholder_id, publishable)
                else:
                    await bus.publish_token(thread_id, placeholder_id, publishable, epoch=epoch)

    if summary_filter is not None:
        pending = summary_filter.finish()
        if pending and (is_current_epoch is None or is_current_epoch()):
            if epoch is None:
                await bus.publish_token(thread_id, placeholder_id, pending)
            else:
                await bus.publish_token(thread_id, placeholder_id, pending, epoch=epoch)

    return TurnStreamOutput(
        stdout_lines=stdout_text,
        stderr_lines=reader.stderr_lines,
        returncode=reader.returncode,
        session_id=captured_session_id,
        t_first_token=t_first_token,
        deltas=deltas,
        result_text=result_text,
        json_lines=chat_json,
    )
