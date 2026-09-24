"""Staff runner: turns a run request into a CLI subprocess and records it.

Lifecycle of one run (all state lives in ``store.RunStore``):

  queued ─▶ preparing ─▶ running ─▶ succeeded | failed | cancelled
                 └────────────────▶ blocked   (someone else holds the issue claim)

``preparing`` = resolve the repository checkout, create an isolated git
worktree, run the Repository_Management lease ritual as subprocesses.
``running``   = the provider CLI is alive; stdout lines become events.

Concurrency is bounded by ``STAFF_MAX_CONCURRENT_RUNS`` (default 3) with one
daemon thread per run; the semaphore is held only while the CLI runs.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff import consolidation, workspace
from staff import focus as focus_mod
from staff import lease as lease_ritual
from staff import usage as usage_mod
from staff.adapters import ADAPTERS, ProviderAdapter
from staff.roles import RoleSpec, load_roles
from staff.store import RunRecord, RunStore, _now, get_store
from staff.watchdog import StaffWatchdog, terminate_process_group

log = logging.getLogger("dashboard.staff.runner")

MAX_CONCURRENT_RUNS = int(os.environ.get("STAFF_MAX_CONCURRENT_RUNS", "3"))
NO_RESULT_ERROR = "agent exited 0 without a STAFF_RESULT line (it stopped before finishing, e.g. to ask a question)"
RUN_TIMEOUT_SECONDS = int(os.environ.get("STAFF_RUN_TIMEOUT_SECONDS", str(4 * 3600)))
IDLE_TIMEOUT_SECONDS = int(os.environ.get("STAFF_IDLE_TIMEOUT_SECONDS", str(20 * 60)))
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


@dataclass(frozen=True)
class RunRequest:
    """Validated, flat run request (built by the router from the POST body)."""

    role: str
    provider: str | None = None
    model: str | None = None
    repo: str = ""
    issue: int | None = None
    pr: int | None = None
    prompt: str = ""
    machine: str = "local"
    requested_by: str = ""
    # PR-consolidation decision from ``staff.consolidation.decide`` (#1213); None when not applicable.
    consolidation: dict[str, Any] | None = None

    @property
    def target_kind(self) -> str:
        if self.issue:
            return "issue"
        if self.pr:
            return "pr"
        return "prompt"

    @property
    def target_ref(self) -> str:
        if self.issue:
            return f"#{self.issue}"
        if self.pr:
            return f"PR #{self.pr}"
        return ""


@dataclass(frozen=True)
class RunPlan:
    """What a run *would* do; returned by dry runs and used by the worker."""

    role: str
    provider: str
    model: str | None
    repo: str
    target_kind: str
    target_ref: str
    operator_prompt: str
    prompt: str
    argv: list[str]
    branch: str
    lease_ritual: bool
    consolidation: dict[str, Any] | None = None
    focus: str = ""  # board priorities + directives for this repo (#1239)

    @property
    def strategy_mode(self) -> str:
        return str(self.consolidation.get("mode", "")) if self.consolidation else ""

    @property
    def consolidation_paragraph(self) -> str:
        return consolidation.prompt_paragraph(self.consolidation) if self.consolidation else ""

    @property
    def issue_number(self) -> str:
        return self.target_ref.lstrip("#") if self.target_kind == "issue" else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "repo": self.repo,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "prompt": self.prompt,
            "argv": list(self.argv),
            "branch": self.branch,
            "lease_ritual": self.lease_ritual,
            "focus": self.focus,
            "consolidation": dict(self.consolidation) if self.consolidation else None,
        }


class StaffRunner:
    """Owns run threads and the process table for this node."""

    def __init__(
        self,
        store: RunStore | None = None,
        roles_loader: Callable[[], dict[str, RoleSpec]] = load_roles,
        adapters: dict[str, ProviderAdapter] | None = None,
        machine: str | None = None,
        focus_loader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._store = store
        self._focus_loader = focus_loader
        self._roles_loader = roles_loader
        self._adapters = adapters if adapters is not None else ADAPTERS
        self.machine = machine or os.environ.get("DISPLAY_NAME") or platform.node() or "local"
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._cancel_flags: set[str] = set()
        self._lock = threading.Lock()
        self._sema = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)

    @property
    def store(self) -> RunStore:
        return self._store or get_store()

    def roles(self) -> dict[str, RoleSpec]:
        return self._roles_loader()

    # ── planning ─────────────────────────────────────────────────────────
    def plan(self, req: RunRequest) -> RunPlan:
        """Resolve role + provider + prompt without side effects.

        Raises ``ValueError`` with an operator-readable message on bad input.
        """
        role = self._resolve_role(req)
        provider = self._resolve_provider(req, role)
        if req.repo and not _SAFE_REF.match(req.repo):
            raise ValueError("repo must be a bare repository name")
        if role.repos and req.repo and req.repo not in role.repos:
            raise ValueError(f"role '{req.role}' is scoped to {', '.join(role.repos)}")
        if not (req.issue or req.pr or req.prompt.strip()):
            raise ValueError("one of issue, pr or prompt is required")
        branch = f"staff/{role.name}-{req.issue or req.pr or 'task'}-{uuid.uuid4().hex[:6]}"
        lease = bool(role.permissions.get("lease", True)) and bool(req.issue) and bool(req.repo)
        paragraph = consolidation.prompt_paragraph(req.consolidation) if req.consolidation else ""
        focus = focus_mod.focus_paragraph(req.repo, focus_mod.load_items(self._focus_loader)) if req.repo else ""
        prompt = workspace.compose_prompt(
            role,
            repo=req.repo,
            target_ref=req.target_ref,
            operator_prompt=req.prompt,
            branch=branch,
            consolidation=paragraph,
            focus=focus,
        )
        argv = self._adapters[provider].build_command(prompt, "<workdir>", req.model or role.model)
        return RunPlan(
            role=role.name,
            provider=provider,
            model=req.model or role.model,
            repo=req.repo,
            target_kind=req.target_kind,
            target_ref=req.target_ref,
            operator_prompt=req.prompt,
            prompt=prompt,
            argv=argv,
            branch=branch,
            lease_ritual=lease,
            consolidation=dict(req.consolidation) if req.consolidation else None,
            focus=focus,
        )

    def _resolve_role(self, req: RunRequest) -> RoleSpec:
        role = self.roles().get(req.role)
        if role is None:
            raise ValueError(f"unknown role '{req.role}'")
        if not role.dispatchable:
            reason = (
                f"schema errors: {'; '.join(role.errors)}"
                if not role.valid
                else f"surface={role.surface}, retired={role.retired}"
            )
            raise ValueError(f"role '{req.role}' is not dispatchable from the dashboard ({reason})")
        return role

    def _resolve_provider(self, req: RunRequest, role: RoleSpec) -> str:
        provider = req.provider or self._first_available(role.providers)
        if provider not in self._adapters:
            raise ValueError(f"unknown provider '{provider}'")
        if req.provider and req.provider not in role.providers and role.name != "ad-hoc":
            allowed = ", ".join(role.providers)
            raise ValueError(f"provider '{req.provider}' is not allowed for role '{role.name}' (allowed: {allowed})")
        return provider

    def _first_available(self, providers: tuple[str, ...]) -> str:
        for pid in providers:
            adapter = self._adapters.get(pid)
            if adapter is not None and adapter.installed():
                return pid
        return providers[0] if providers else "claude"

    # ── submission ───────────────────────────────────────────────────────
    def submit(self, req: RunRequest) -> RunRecord:
        """Validate, persist as ``queued`` and start the worker thread."""
        plan = self.plan(req)
        rec = RunRecord(
            id=f"run-{uuid.uuid4().hex[:12]}",
            role=plan.role,
            provider=plan.provider,
            model=plan.model,
            machine=self.machine,
            repo=plan.repo,
            target_kind=plan.target_kind,
            target_ref=plan.target_ref,
            prompt=plan.prompt,
            requested_by=req.requested_by,
            branch=plan.branch,
            strategy_mode=plan.strategy_mode,
        )
        self.store.create_run(rec)
        self.store.append_event(rec.id, "queued", f"queued on {self.machine} for {plan.provider}")
        thread = threading.Thread(target=self._worker, args=(rec, plan), name=f"staff-{rec.id}", daemon=True)
        thread.start()
        return rec

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            self._cancel_flags.add(run_id)
            proc = self._procs.get(run_id)
        rec = self.store.get_run(run_id)
        if rec is None:
            return False
        if proc is not None and proc.poll() is None:
            terminate_process_group(proc.pid, grace_period=1.0)
            self.store.append_event(run_id, "cancel", "terminate signal sent")
            return True
        if rec.status in ("queued", "preparing"):
            self.store.update_run(run_id, status="cancelled", ended_at=_now())
            self.store.append_event(run_id, "cancel", "cancelled before start")
            return True
        return False

    # ── worker ───────────────────────────────────────────────────────────
    def _worker(self, rec: RunRecord, plan: RunPlan) -> None:
        store = self.store
        with self._sema:
            if rec.id in self._cancel_flags:
                return
            try:
                store.update_run(rec.id, status="preparing", started_at=_now())
                workdir = self._prepare_workdir(rec, plan)
                lease_note = ""
                agent = self._adapters[plan.provider].lease_agent
                if plan.lease_ritual:
                    note = lease_ritual.acquire(
                        store,
                        rec.id,
                        repo=plan.repo,
                        issue=plan.issue_number,
                        agent=agent,
                        branch=plan.branch,
                    )
                    if note is None:
                        return  # blocked; status already recorded
                    lease_note = note
                self._execute(rec, plan, workdir, lease_note)
            except Exception as exc:  # noqa: BLE001
                log.exception("staff run %s crashed", rec.id)
                store.update_run(rec.id, status="failed", ended_at=_now(), error=str(exc)[:1000])
                store.append_event(rec.id, "error", str(exc)[:1000])
            finally:
                if plan.lease_ritual:
                    lease_ritual.release(
                        store,
                        rec.id,
                        repo=plan.repo,
                        issue=plan.issue_number,
                        agent=self._adapters[plan.provider].lease_agent,
                    )

    def _prepare_workdir(self, rec: RunRecord, plan: RunPlan) -> Path:
        store = self.store
        if not plan.repo:
            workdir = workspace.staff_worktrees_root() / rec.id
            workdir.mkdir(parents=True, exist_ok=True)
            store.update_run(rec.id, workdir=str(workdir))
            return workdir
        checkout = workspace.find_repo_checkout(plan.repo)
        if checkout is None:
            store.append_event(rec.id, "clone", f"no local checkout of {plan.repo}; cloning")
            checkout = workspace.clone_repo(plan.repo)
        worktree = workspace.staff_worktrees_root() / f"{plan.repo}-{rec.id}"
        workspace.add_worktree(checkout, worktree, plan.branch)
        store.update_run(rec.id, workdir=str(worktree))
        store.append_event(rec.id, "worktree", f"{worktree} on {plan.branch}")
        return worktree

    def _execute(self, rec: RunRecord, plan: RunPlan, workdir: Path, lease_note: str) -> None:
        store = self.store
        adapter = self._adapters[plan.provider]
        role = self.roles()[plan.role]
        prompt = workspace.compose_prompt(
            role,
            repo=plan.repo,
            target_ref=plan.target_ref,
            operator_prompt=plan.operator_prompt,
            branch=plan.branch,
            lease_note=lease_note,
            consolidation=plan.consolidation_paragraph,
            focus=plan.focus,
        )
        argv = adapter.build_command(prompt, str(workdir), plan.model)
        transcript = workdir / ".staff" / "transcript.log"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            **adapter.runtime_env(),
            "STAFF_RUN_ID": rec.id,
            "STAFF_ROLE": plan.role,
        }
        exe = shutil.which(adapter.executable) or adapter.executable
        proc = subprocess.Popen(  # noqa: S603
            [exe, *argv[1:]],
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
        with self._lock:
            self._procs[rec.id] = proc
            cancelled = rec.id in self._cancel_flags
        store.update_run(
            rec.id,
            status="running",
            transcript_path=str(transcript),
            prompt=prompt,
            pid=proc.pid,
        )
        store.append_event(rec.id, "start", f"{adapter.executable} ({plan.provider}) in {workdir}")
        if cancelled:
            terminate_process_group(proc.pid, grace_period=1.0)
        if adapter.prompt_via_stdin and proc.stdin is not None:
            proc.stdin.write(prompt + "\n")
            proc.stdin.close()

        wall_clock_timeout = (
            float(os.environ["STAFF_RUN_TIMEOUT_SECONDS"])
            if "STAFF_RUN_TIMEOUT_SECONDS" in os.environ
            else role.budget_max_minutes * 60.0
        )
        idle_timeout = (
            float(os.environ["STAFF_IDLE_TIMEOUT_SECONDS"])
            if "STAFF_IDLE_TIMEOUT_SECONDS" in os.environ
            else role.idle_minutes * 60.0
        )

        watchdog = StaffWatchdog(
            run_id=rec.id,
            proc=proc,
            store=store,
            max_seconds=wall_clock_timeout,
            idle_seconds=idle_timeout,
        )
        watchdog.start()
        try:
            usage, result_line = self._pump_output(rec, adapter, proc, transcript, watchdog)
            try:
                rc = proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                rc = -1
        finally:
            watchdog.stop()

        with self._lock:
            self._procs.pop(rec.id, None)
        cancelled = rec.id in self._cancel_flags
        failure_class = watchdog.failure_class or ""
        if cancelled:
            status = "cancelled"
            error = ""
        elif failure_class:
            status = "failed"
            error = watchdog.error_message or f"run terminated ({failure_class})"
        elif rc == 0 and not result_line:
            # An unattended agent that stops to ask a question exits 0 without finishing (DeskComputer 2026-09-22).
            status, error = "failed", NO_RESULT_ERROR
        elif rc == 0:
            status, error = "succeeded", ""
        else:
            status, error = "failed", ""

        store.update_run(
            rec.id,
            status=status,
            failure_class=failure_class,
            error=error,
            ended_at=_now(),
            exit_code=rc,
            cost_usd=float(usage.get("cost_usd", 0.0)),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            outcome=consolidation.parse_outcome(result_line),  # issue #1213
        )
        usage_mod.finalize_cost(store, rec.id, plan.provider, plan.model)  # issue #1200
        store.append_event(rec.id, "exit", f"exit code {rc} → {status}")

    def _pump_output(
        self,
        rec: RunRecord,
        adapter: ProviderAdapter,
        proc: subprocess.Popen[str],
        transcript: Path,
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
                tf.write(line)
                event = adapter.parse_line(line)
                if event.get("usage"):
                    usage.update(event["usage"])
                text = event.get("text") or ""
                if text.strip():
                    self.store.append_event(rec.id, event.get("kind", "text"), text)
                    if "STAFF_RESULT:" in text:
                        result_line = text[text.index("STAFF_RESULT:") :]
        return usage, result_line


_runner: StaffRunner | None = None
_runner_lock = threading.Lock()


def get_runner() -> StaffRunner:
    global _runner  # noqa: PLW0603
    with _runner_lock:
        if _runner is None:
            _runner = StaffRunner()
        return _runner


def reset_runner() -> None:
    global _runner  # noqa: PLW0603
    with _runner_lock:
        _runner = None
