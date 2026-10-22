"""Staff runner: CLI subprocess lifecycle (queued → running → terminal status)."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from staff import consolidation, review, verification, workspace
from staff import focus as focus_mod
from staff import lease as lease_ritual
from staff import retry as retry_mod
from staff import usage as usage_mod
from staff.adapters import ADAPTERS, ProviderAdapter
from staff.execution_result import classify_execution_result
from staff.plan import RunPlan, RunRequest
from staff.roles import RoleSpec, load_roles
from staff.run_link import handle_run_status_change, result_summary
from staff.runner_ops import (
    LaunchPaths,
    apply_staff_run_fleet_token,
    apply_staff_run_github_child_env,
    execute_retry_nudge,
    extract_transcript_question,
    fail_if_cli_outdated,
    new_run_record,
    prepare_run_workdir,
    pump_output,
    read_only_kwargs,
    resolve_launch_paths,
    select_first_available_provider,
)
from staff.store import RunRecord, RunStore, _now, get_store
from staff.tokens import revoke_run_token
from staff.watchdog import StaffWatchdog, terminate_process_group

log = logging.getLogger("dashboard.staff.runner")

MAX_CONCURRENT_RUNS = int(os.environ.get("STAFF_MAX_CONCURRENT_RUNS", "3"))
NO_RESULT_ERROR = "agent exited 0 without a STAFF_RESULT line"
RUN_TIMEOUT_SECONDS = int(os.environ.get("STAFF_RUN_TIMEOUT_SECONDS", str(4 * 3600)))
IDLE_TIMEOUT_SECONDS = int(os.environ.get("STAFF_IDLE_TIMEOUT_SECONDS", str(20 * 60)))
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


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
        self._provider_semas: dict[str, threading.BoundedSemaphore] = {}

    def _get_provider_sema(self, provider: str) -> threading.BoundedSemaphore:
        with self._lock:
            if provider not in self._provider_semas:
                max_c = max(1, getattr(self._adapters.get(provider), "max_concurrency", 1))
                self._provider_semas[provider] = threading.BoundedSemaphore(max_c)
            return self._provider_semas[provider]

    @property
    def store(self) -> RunStore:
        return self._store or get_store()

    def opens_pr(self, role: str) -> bool:
        """Whether ``role`` is expected to open a PR (its ``permissions.open_pr``; #1516)."""
        spec = self.roles().get(role)
        return spec is not None and spec.opens_pr

    def roles(self) -> dict[str, RoleSpec]:
        return self._roles_loader()

    # ── planning ─────────────────────────────────────────────────────────
    def plan(self, req: RunRequest) -> RunPlan:
        """Resolve role + provider + prompt without side effects.

        Raises ``ValueError`` with an operator-readable message on bad input.
        """
        role = self._resolve_role(req)
        from staff.failover_dispatch import resolve_provider_and_model

        provider, _chain_model, model_tier = resolve_provider_and_model(self._adapters, self.machine, req, role)
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
        from staff.dispatch_bundle import DispatchContractError, resolve_dispatch_bundle

        try:
            bundle = resolve_dispatch_bundle(
                role=role.name,
                repo=req.repo,
                task_type=req.task_type,
                standards_extra=req.standards_extra,
            )
        except DispatchContractError as exc:
            raise ValueError(str(exc)) from exc
        references_block = ""
        library_work_ids: tuple[int, ...] = ()
        if role.library_references and req.repo:
            from library.db import library_db_path
            from library.dispatch_refs import attach_library_references, reference_query_from_task

            db = library_db_path()
            if db.is_file():
                references_block, library_work_ids = attach_library_references(
                    db, repo=req.repo, query=reference_query_from_task(req)
                )
        prompt = workspace.compose_prompt(
            role,
            repo=req.repo,
            target_ref=req.target_ref,
            operator_prompt=req.prompt,
            branch=branch,
            consolidation=paragraph,
            focus=focus,
            dispatch_bundle=bundle,
            library_references=references_block,
        )
        from staff.failover_model import resolve_launch_model, role_base_tier
        from staff.provider_failover_config import resolve_failover_chain

        adapter = self._adapters[provider]
        chain = resolve_failover_chain(role, requested_provider=provider if req.provider else None)
        step = next((s for s in chain if s.provider == provider), None)
        effective_model, resolved_tier = resolve_launch_model(
            req=req,
            role=role,
            provider=provider,
            adapter=adapter,
            step=step,
            active_tier=role_base_tier(role),
        )
        argv = adapter.build_command(prompt, "<workdir>", effective_model, **read_only_kwargs(role))
        return RunPlan(
            role=role.name,
            provider=provider,
            model=effective_model,
            model_tier=resolved_tier or model_tier,
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
            pr_lifecycle=req.pr_lifecycle,
            head_ref=req.head_ref,
            skill_pin=bundle.skill_pin,
            standards_version=bundle.standards_version,
            library_work_ids=library_work_ids,
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

    def _first_available(self, providers: tuple[str, ...], ceiling: float | None = None) -> str:
        return select_first_available_provider(self._adapters, providers, ceiling)

    @staticmethod
    def _launch_paths(adapter: ProviderAdapter, workdir: Path) -> LaunchPaths:
        return resolve_launch_paths(adapter, workdir)

    def submit(self, req: RunRequest) -> RunRecord:
        """Validate, persist as ``queued`` and start the worker thread."""
        rec, plan, _created = self.admit(req)
        self.launch(rec, plan)
        return rec

    def admit(self, req: RunRequest, *, run_id: str | None = None) -> tuple[RunRecord, RunPlan, bool]:
        """Validate and persist as ``queued`` without starting a worker (#1796).

        A ``run_id`` that already has a row returns that row with ``created=False``, so
        a retried operation is admitted once. Call :meth:`launch` only when created.
        """
        plan = self.plan(req)
        if run_id and (existing := self.store.get_run(run_id)) is not None:
            return existing, plan, False
        role = self.roles().get(plan.role)
        rec = new_run_record(plan, req, self.machine, getattr(role, "max_attempts", 2) or 2, run_id)
        try:
            self.store.create_run(rec)
        except sqlite3.IntegrityError:
            if (existing := self.store.get_run(rec.id)) is None:
                raise
            return existing, plan, False
        self.store.append_event(rec.id, "queued", f"queued on {self.machine} for {plan.provider}")
        handle_run_status_change(rec, "queued")
        return rec, plan, True

    def launch(self, rec: RunRecord, plan: RunPlan) -> None:
        """Start the worker thread for a run :meth:`admit` created."""
        threading.Thread(target=self._worker, args=(rec, plan), name=f"staff-{rec.id}", daemon=True).start()

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
            handle_run_status_change(rec, "cancelled")
            return True
        if rec.status in ("queued", "preparing"):
            self.store.update_run(run_id, status="cancelled", ended_at=_now())
            self.store.append_event(run_id, "cancel", "cancelled before start")
            handle_run_status_change(rec, "cancelled")
            return True
        return False

    # ── worker ───────────────────────────────────────────────────────────
    def _worker(self, rec: RunRecord, plan: RunPlan) -> None:
        store = self.store
        provider_sema = self._get_provider_sema(plan.provider)
        with provider_sema, self._sema:
            if rec.id in self._cancel_flags:
                return
            try:
                store.update_run(rec.id, status="preparing", started_at=_now())
                handle_run_status_change(rec, "preparing")
                if fail_if_cli_outdated(store, rec, self._adapters[plan.provider]):
                    return  # below the CLI floor: no worktree, no lease (#1680)
                workdir = prepare_run_workdir(store, rec, plan)
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
        retry_mod.handle_post_execution_retry(self, rec, plan)

    def _prepare_workdir(self, rec: RunRecord, plan: RunPlan) -> Path:
        return prepare_run_workdir(self.store, rec, plan)

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
        argv = adapter.build_command(
            prompt, str(workdir), plan.model, **read_only_kwargs(role), **self._launch_paths(adapter, workdir)
        )
        transcript = workdir / ".staff" / "transcript.log"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        wall_clock_timeout = float(os.environ.get("STAFF_RUN_TIMEOUT_SECONDS", role.budget_max_minutes * 60.0))
        idle_timeout = float(os.environ.get("STAFF_IDLE_TIMEOUT_SECONDS", role.idle_minutes * 60.0))
        real_gh = shutil.which("gh") or "gh"

        env = apply_staff_run_github_child_env(
            store,
            rec,
            os.environ,
            role,
            plan.repo or "",
            run_id=rec.id,
            real_gh_path=real_gh,
        )
        if env is None:
            return
        env = apply_staff_run_fleet_token(store, rec, plan, role, env, adapter, wall_clock_timeout=wall_clock_timeout)
        if env is None:
            return
        try:
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
            store.update_run(rec.id, status="running", transcript_path=str(transcript), prompt=prompt, pid=proc.pid)
            store.append_event(rec.id, "start", f"{adapter.executable} ({plan.provider}) in {workdir}")
            handle_run_status_change(rec, "running")
            if cancelled:
                terminate_process_group(proc.pid, grace_period=1.0)
            if adapter.prompt_via_stdin and proc.stdin is not None:
                proc.stdin.write(prompt + "\n")
                proc.stdin.close()

            watchdog = StaffWatchdog(
                run_id=rec.id,
                proc=proc,
                store=store,
                max_seconds=wall_clock_timeout,
                idle_seconds=idle_timeout,
            )
            watchdog.start()
            try:
                pump_res = self._pump_output(rec, adapter, proc, transcript, watchdog)
                usage, result_line = pump_res
                session_id = getattr(pump_res, "session_id", None)
                try:
                    rc = proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    rc = -1
            finally:
                watchdog.stop()

            with self._lock:
                self._procs.pop(rec.id, None)
            cancelled = rec.id in self._cancel_flags
            question = extract_transcript_question(transcript)

            if not cancelled and rc == 0 and not result_line and not question:
                n_line, n_usage, n_rc = execute_retry_nudge(
                    rec=rec,
                    adapter=adapter,
                    plan=plan,
                    role=role,
                    workdir=workdir,
                    env=env,
                    session_id=session_id,
                    transcript=transcript,
                    store=store,
                    wall_clock_timeout=wall_clock_timeout,
                    idle_timeout=idle_timeout,
                    lock=self._lock,
                    procs=self._procs,
                    cancel_flags=self._cancel_flags,
                )
                if n_line:
                    result_line = n_line
                elif n_rc != 0:
                    rc = n_rc
                if n_usage:
                    for k in ("input_tokens", "output_tokens"):
                        usage[k] = int(usage.get(k, 0)) + int(n_usage.get(k, 0))
                    usage["cost_usd"] = float(usage.get("cost_usd", 0.0)) + float(n_usage.get("cost_usd", 0.0))
            status, failure_class, retryable, remediation, error = classify_execution_result(
                cancelled=cancelled,
                rc=rc,
                result_line=result_line,
                provider=plan.provider,
                transcript_path=transcript,
                watchdog_failure_class=watchdog.failure_class or "",
                watchdog_error=watchdog.error_message or "",
                machine=self.machine,
                has_thread=bool(rec.thread_id),
            )

            same_provider = review.is_same_provider_review(rec.prompt)
            if rec.role == "code-reviewer" and rc == 0:
                review_verdict = review.parse_review_verdict(result_line, same_provider=same_provider)
                if review_verdict.status == "needs_input":
                    status = failure_class = "needs_input"
                    remediation = "Review completed without emitting a STAFF_RESULT verdict line."
                elif review_verdict.status == "failed":
                    status, failure_class = "failed", "invalid_output"
                    error = review_verdict.error or "Malformed review verdict line"
                    remediation = "Review completed with a malformed STAFF_RESULT line."

            # The exit event lands before the terminal status, so a reader that sees the status sees it (#1489).
            store.append_event(rec.id, "exit", f"exit code {rc} → {status}")
            store.update_run(
                rec.id,
                status=status,
                failure_class=failure_class,
                retryable=retryable,
                remediation=remediation,
                error=error,
                ended_at=_now(),
                exit_code=rc,
                cost_usd=float(usage.get("cost_usd", 0.0)),
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                outcome=review.parse_outcome(result_line, same_provider=same_provider)
                or consolidation.parse_outcome(result_line),
            )
            usage_mod.finalize_cost(store, rec.id, plan.provider, plan.model)
            verdict = verification.verify_and_record(store, rec.id, opens_pr=lambda: self.opens_pr(rec.role))
            updated_rec = store.get_run(rec.id)
            if updated_rec is not None:
                updated_rec = verification.apply_deliverable_failure(
                    store,
                    updated_rec,
                    verdict,
                    opens_pr=self.opens_pr(rec.role),
                )
            if updated_rec is not None:
                q = question if status == "needs_input" else None
                if status == "succeeded":
                    from staff.failover_dispatch import run_completion_summary

                    summary = run_completion_summary(
                        store,
                        updated_rec,
                        result_line,
                        provider=plan.provider,
                        model=plan.model,
                    )
                else:
                    summary = (updated_rec.outcome or result_summary(result_line)) or ""
                handle_run_status_change(
                    updated_rec,
                    status=status,
                    question=q,
                    summary=summary,
                )
        finally:
            revoke_run_token(rec.id)

    def _pump_output(
        self,
        rec: RunRecord,
        adapter: ProviderAdapter,
        proc: subprocess.Popen[str],
        transcript: Path,
        watchdog: StaffWatchdog | None = None,
    ) -> Any:
        return pump_output(rec, adapter, proc, transcript, self.store, watchdog)


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
