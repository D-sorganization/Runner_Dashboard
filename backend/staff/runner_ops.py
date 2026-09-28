"""Low-level execution helpers for StaffRunner (#1586, #1587, #1588, #1593).

Extracted from staff.runner to keep file lengths within the 500-line budget.
Contains provider selection, launch arguments resolution, and subprocess output pumping.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

import provider_switch
from staff import cli_version, workspace
from staff import quota as quota_mod
from staff.adapters import ProviderAdapter
from staff.redaction import redact_sensitive_content
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore, _now
from staff.watchdog import StaffWatchdog


def can_run_unattended(adapters: Mapping[str, ProviderAdapter], pid: str) -> bool:
    """Known, not chat-only (#1586), not switched off on this node (#1597), CLI not below its floor (#1680)."""
    return (
        pid in adapters
        and getattr(adapters[pid], "unattended", True)
        and not provider_switch.is_disabled(pid)
        and not cli_version.is_outdated(getattr(adapters[pid], "executable", ""))
    )


def fail_if_cli_outdated(store: RunStore, rec: RunRecord, adapter: ProviderAdapter) -> bool:
    """Fail ``rec`` up front when the adapter's CLI is below its minimum version (#1680).

    Post: when True, the run is ``failed`` with the non-retryable ``cli_outdated`` class
    and nothing was launched; when False, the run is untouched.
    """
    gate = cli_version.version_gate(getattr(adapter, "executable", ""))
    if gate is None:
        return False
    store.update_run(
        rec.id,
        status="failed",
        failure_class=gate.failure_class,
        retryable=gate.retryable,
        remediation=gate.remediation,
        error=gate.error,
        ended_at=_now(),
    )
    store.append_event(rec.id, "error", gate.remediation)
    return True


def select_first_available_provider(
    adapters: Mapping[str, ProviderAdapter],
    providers: tuple[str, ...],
    ceiling: float | None = None,
) -> str:
    """First installed, unattended provider whose plan is under ``ceiling`` percent (#1586, #1588).

    Post: when every installed provider is over quota, the first installed one
    (the gates in the scheduler and dispatch decide whether it may run); when
    none is installed, the first that can run unattended.
    """
    runnable = [pid for pid in providers if can_run_unattended(adapters, pid)]
    installed = [pid for pid in runnable if adapters[pid].installed()]
    limit = quota_mod.ceiling_percent(None) if ceiling is None else ceiling
    for pid in installed:
        if quota_mod.headroom(pid, limit)[0]:
            return pid
    if installed:
        return installed[0]
    if runnable:
        return runnable[0]
    return providers[0] if providers else "claude"


class ReadOnlyKwargs(TypedDict, total=False):
    read_only: bool


class LaunchPaths(TypedDict, total=False):
    gitdir: str
    policy: str


def read_only_kwargs(role: RoleSpec) -> ReadOnlyKwargs:
    """``read_only`` keyword for ``adapter.build_command`` (#1659), passed only when set.

    Post: empty for a normal role, so an adapter with the older signature still works.
    """
    return {"read_only": True} if role.code_read_only else {}


def resolve_launch_paths(adapter: ProviderAdapter, workdir: Path) -> LaunchPaths:
    """``gitdir``/``policy`` keyword arguments for ``adapter.build_command`` (#1586).

    Post: only the slots the adapter's argv actually uses are passed, so an
    adapter with the older ``build_command(prompt, workdir, model)`` shape still works.
    """
    argv = getattr(adapter, "argv", ())
    paths: LaunchPaths = {}
    if any("{gitdir}" in part for part in argv):
        paths["gitdir"] = str(workspace.git_common_dir(workdir))
    policy_text = getattr(adapter, "policy_text", None)
    if policy_text is not None and any("{policy}" in part for part in argv):
        paths["policy"] = str(workspace.write_policy_file(adapter.provider_id, policy_text()))
    return paths


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
                store.append_event(rec.id, event.get("kind", "text"), text)
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

    store.append_event(rec.id, "nudge", "session exited 0 without STAFF_RESULT; nudging for result line (#1709)")
    try:
        launch_paths = resolve_launch_paths(adapter, workdir)
        ro_kwargs: ReadOnlyKwargs = read_only_kwargs(role) if role is not None else {}
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
