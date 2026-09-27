"""Low-level execution helpers for StaffRunner (#1586, #1587, #1588, #1593).

Extracted from staff.runner to keep file lengths within the 500-line budget.
Contains provider selection, launch arguments resolution, and subprocess output pumping.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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


def read_only_kwargs(role: RoleSpec) -> dict[str, bool]:
    """``read_only`` keyword for ``adapter.build_command`` (#1659), passed only when set.

    Post: empty for a normal role, so an adapter with the older signature still works.
    """
    return {"read_only": True} if role.code_read_only else {}


def resolve_launch_paths(adapter: ProviderAdapter, workdir: Path) -> dict[str, str]:
    """``gitdir``/``policy`` keyword arguments for ``adapter.build_command`` (#1586).

    Post: only the slots the adapter's argv actually uses are passed, so an
    adapter with the older ``build_command(prompt, workdir, model)`` shape still works.
    """
    argv = getattr(adapter, "argv", ())
    paths: dict[str, str] = {}
    if any("{gitdir}" in part for part in argv):
        paths["gitdir"] = str(workspace.git_common_dir(workdir))
    policy_text = getattr(adapter, "policy_text", None)
    if policy_text is not None and any("{policy}" in part for part in argv):
        paths["policy"] = str(workspace.write_policy_file(adapter.provider_id, policy_text()))
    return paths


def pump_output(
    rec: RunRecord,
    adapter: ProviderAdapter,
    proc: subprocess.Popen[str],
    transcript: Path,
    store: RunStore,
    watchdog: StaffWatchdog | None = None,
) -> tuple[dict[str, Any], str]:
    """Stream stdout lines into the transcript file and the event store.

    Returns the usage the adapter reported and the last ``STAFF_RESULT:`` text seen.
    """
    usage: dict[str, Any] = {}
    result_line = ""
    assert proc.stdout is not None  # noqa: S101
    with transcript.open("a", encoding="utf-8") as tf:
        for line in proc.stdout:
            if watchdog is not None:
                watchdog.record_output()
            tf.write(redact_sensitive_content(line))
            event = adapter.parse_line(line)
            if event.get("usage"):
                usage.update(event["usage"])
            if event.get("kind") == "rate_limit_event" and isinstance(event.get("raw"), dict):
                quota_mod.observe(rec.provider, event["raw"])  # live plan windows, free (#1587)
            text = event.get("text") or ""
            if text.strip():
                store.append_event(rec.id, event.get("kind", "text"), text)
                if "STAFF_RESULT:" in text:
                    result_line = text[text.index("STAFF_RESULT:") :]
    return usage, result_line
