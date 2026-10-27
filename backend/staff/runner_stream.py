"""Output pumping and the STAFF_RESULT retry nudge for staff runs.

Moved from ``staff.runner_ops`` to give it headroom under the 500-line cap (#2803);
``staff.runner_ops`` re-exports every name here, so existing imports are unchanged.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from staff import quota as quota_mod
from staff.adapter_policies import antigravity_permissions
from staff.adapters import ProviderAdapter
from staff.redaction import redact_sensitive_content
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore
from staff.watchdog import StaffWatchdog

if TYPE_CHECKING:
    from staff.runner_ops import ReadOnlyKwargs


RESULT_NUDGE_PROMPT = "Please emit your final result line now. Reply ONLY with exact prefix: STAFF_RESULT: <summary>"


class PumpResult(tuple[dict[str, Any], str]):
    """(usage, result_line) tuple for backward compatibility with optional session_id."""

    usage: dict[str, Any]
    result_line: str
    session_id: str | None

    def __new__(cls, usage: dict[str, Any], result_line: str, session_id: str | None = None) -> PumpResult:
        inst = super().__new__(cls, (usage, result_line))
        inst.usage = usage
        inst.result_line = result_line
        inst.session_id = session_id
        return inst


def pump_output(
    rec: RunRecord,
    adapter: ProviderAdapter,
    proc: subprocess.Popen[str],
    transcript: Path,
    store: RunStore,
    watchdog: StaffWatchdog | None = None,
) -> PumpResult:
    """Stream stdout lines into the transcript file and the event store.

    Returns the usage the adapter reported, the last ``STAFF_RESULT:`` text seen,
    and any captured session_id (#1709).
    """
    from staff.chat_history import extract_session_id

    usage: dict[str, Any] = {}
    result_line = ""
    session_id: str | None = None
    assert proc.stdout is not None  # noqa: S101
    with transcript.open("a", encoding="utf-8") as tf:
        for line in proc.stdout:
            if watchdog is not None:
                watchdog.record_output()
            tf.write(redact_sensitive_content(line))
            event = adapter.parse_line(line)
            if not session_id:
                pid = getattr(adapter, "provider_id", getattr(rec, "provider", ""))
                session_id = extract_session_id(pid, event, raw_line=line)
            if event.get("usage"):
                usage.update(event["usage"])
            if event.get("kind") == "rate_limit_event" and isinstance(event.get("raw"), dict):
                quota_mod.observe(rec.provider, event["raw"])  # live plan windows, free (#1587)
            text = event.get("text") or ""
            if text.strip():
                store.append_event(
                    rec.id,
                    event.get("kind", "text"),
                    text,
                    lesson_evidence=event.get("lesson_evidence"),
                )
                if "STAFF_RESULT:" in text:
                    result_line = text[text.index("STAFF_RESULT:") :]
    return PumpResult(usage, result_line, session_id)


def pump_nudge_output(
    rec: RunRecord,
    adapter: ProviderAdapter,
    proc: subprocess.Popen[str],
    transcript: Path,
    store: RunStore,
    watchdog: StaffWatchdog | None = None,
) -> PumpResult:
    """Stream stdout lines from retry nudge into transcript and event store (#1709).

    Accepts the result line ONLY on an exact prefix match (``text.startswith('STAFF_RESULT:')``).
    """
    usage: dict[str, Any] = {}
    result_line = ""
    assert proc.stdout is not None  # noqa: S101
    with transcript.open("a", encoding="utf-8") as tf:
        tf.write("\n--- [nudge retry for STAFF_RESULT] ---\n")
        for line in proc.stdout:
            if watchdog is not None:
                watchdog.record_output()
            tf.write(redact_sensitive_content(line))
            event = adapter.parse_line(line)
            if event.get("usage"):
                usage.update(event["usage"])
            text = (event.get("text") or "").strip()
            if text:
                store.append_event(rec.id, "nudge_reply", text)
                if text.startswith("STAFF_RESULT:"):
                    result_line = text
    return PumpResult(usage, result_line)


def execute_retry_nudge(
    *,
    rec: RunRecord,
    adapter: ProviderAdapter,
    plan: Any,
    role: RoleSpec | None,
    workdir: Path,
    env: dict[str, str],
    session_id: str | None,
    transcript: Path,
    store: RunStore,
    wall_clock_timeout: float,
    idle_timeout: float,
    lock: Any,
    procs: dict[str, subprocess.Popen[str]],
    cancel_flags: set[str],
) -> tuple[str, dict[str, Any], int]:
    if not session_id:
        return "", {}, 0

    from staff.runner_ops import read_only_kwargs, resolve_launch_paths  # noqa: PLC0415  (runner_ops re-exports us)

    store.append_event(rec.id, "nudge", "session exited 0 without STAFF_RESULT; nudging for result line (#1709)")
    try:
        launch_paths = resolve_launch_paths(adapter, workdir)
        ro_kwargs: ReadOnlyKwargs = read_only_kwargs(role) if role is not None else {}
        if adapter.provider_id == "antigravity":
            antigravity_permissions(
                read_only=bool(ro_kwargs.get("read_only")),
                workdir=workdir,
                gitdir=Path(launch_paths.get("gitdir", str(workdir))),
            )
        nudge_argv = adapter.build_command(
            RESULT_NUDGE_PROMPT,
            str(workdir),
            model=plan.model,
            session_id=session_id,
            **ro_kwargs,
            **launch_paths,
        )
    except Exception as exc:  # noqa: BLE001
        store.append_event(rec.id, "nudge", f"could not build nudge command: {exc}")
        return "", {}, 0

    exe = shutil.which(adapter.executable) or adapter.executable
    try:
        nudge_proc = subprocess.Popen(
            [exe, *nudge_argv[1:]],
            cwd=str(workdir),
            env=env,
            stdin=subprocess.PIPE if adapter.prompt_via_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as exc:  # noqa: BLE001
        store.append_event(rec.id, "nudge", f"could not launch nudge process: {exc}")
        return "", {}, 0

    with lock:
        procs[rec.id] = nudge_proc
        cancelled = rec.id in cancel_flags
    if cancelled:
        from staff.watchdog import terminate_process_group

        terminate_process_group(nudge_proc.pid, grace_period=1.0)
        return "", {}, -1

    if adapter.prompt_via_stdin and nudge_proc.stdin is not None:
        nudge_proc.stdin.write(RESULT_NUDGE_PROMPT + "\n")
        nudge_proc.stdin.close()

    watchdog = StaffWatchdog(
        run_id=rec.id,
        proc=nudge_proc,
        store=store,
        max_seconds=min(wall_clock_timeout, 300),
        idle_seconds=min(idle_timeout, 120),
    )
    watchdog.start()
    try:
        nudge_pump = pump_nudge_output(rec, adapter, nudge_proc, transcript, store, watchdog)
        try:
            rc = nudge_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            rc = -1
    finally:
        watchdog.stop()
        with lock:
            procs.pop(rec.id, None)

    return nudge_pump.result_line, nudge_pump.usage, rc


def extract_transcript_question(transcript: Path) -> str | None:
    """Extract trailing question text from transcript if agent ended on a question."""
    from staff.classifier import _extract_last_line_text  # noqa: PLC0415

    try:
        last = _extract_last_line_text(transcript.read_text(encoding="utf-8", errors="replace"))
        return last if last.endswith("?") else None
    except Exception:  # noqa: BLE001
        return None
