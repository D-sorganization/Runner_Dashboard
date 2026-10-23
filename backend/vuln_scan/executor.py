"""Security scan executor: admission, phase A build, phase B agent run (#2572).

The flow follows panel verdict q4 (``~/backlog-orchestrator/cli/panel/q4-*``):

1. **Admission** (:mod:`vuln_scan.admission`): a refused or dry-run request is
   logged and returns without touching docker or the CLI.
2. **Source** (:mod:`vuln_scan.source`): leftovers of a dead process are swept,
   the SHA arrives as a tarball (no ``.git``, no credentials) and its own
   ``.oss-scanner/`` contract is rechecked; ``disabled: true`` stops the scan.
3. **Phase A** (:mod:`vuln_scan.docker_ops`): ``docker buildx build`` on the
   filtered build network; an image declaring a ``VOLUME`` is refused and the
   digest is recorded. Admission then runs again.
4. **Phase B**: a ``--network=none`` container hardened by
   :func:`vuln_scan.sandbox.run_argv`; the claude CLI runs here in the sanitized
   profile (:mod:`vuln_scan.launch`) with ``scan_sandbox`` as its only tools.
5. **Findings and verify** (:mod:`vuln_scan.agent_io`, #2573): findings are
   stored as ``new`` under the per-scan cap; a second model in a fresh container
   marks each ``confirmed`` or ``inconclusive``, never false positive.

Child processes run under :mod:`vuln_scan.watch` (CI queue and disk re-polled,
CI always wins); cleanup runs in a ``finally`` on cancel, abort and error.
Every external effect is injected (:class:`Probes`, the docker runner, ``popen``).
"""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from staff.store import _now
from vuln_scan import launch, watch
from vuln_scan.admission import Facts, admit, runtime_problems
from vuln_scan.agent_io import (
    AgentStream,
    apply_verdicts,
    ingest_findings,
    scan_prompt,
    verify_prompt,
)
from vuln_scan.docker_ops import (
    SWEEPS,
    build_argv,
    cleanup_steps,
    docker_ids,
    image_digest,
    inspect_argv,
)
from vuln_scan.findings import FindingsStore
from vuln_scan.operator_config import OperatorConfig, ScanHost
from vuln_scan.sandbox import Runner, container_name, image_tag, run_argv
from vuln_scan.scans import HEARTBEAT_S, ScanLog, SlotBusy
from vuln_scan.source import docker_env, local_contract
from vuln_scan.watch import ScanAborted, watch_process

log = logging.getLogger("dashboard.vuln_scan.executor")

SERVER_PATH = Path(__file__).resolve().parent / "scan_mcp.py"


def verify_container(scan_id: str) -> str:
    """The verifier's fresh container: same image, never the scanner's container."""
    return container_name(f"{scan_id}-v")


@dataclass(frozen=True)
class ScanRequest:
    repo: str
    sha: str
    dry_run: bool = True
    requested_by: str = ""


@dataclass
class Probes:
    """Every fact the executor needs from the outside world."""

    default_branch_sha: Callable[[str], str | None]
    contract: Callable[[str], Mapping[str, Any]]
    queued_jobs: Callable[[], int | None]
    free_gb: Callable[[ScanHost], float | None]
    build_filter_ok: Callable[[ScanHost], bool]
    oauth_ok: Callable[[], bool]
    cli_version: Callable[[], str]
    fetch_source: Callable[[str, str, Path], None]
    now: Callable[[], datetime]


class ScanExecutor:
    def __init__(
        self,
        config: OperatorConfig,
        probes: Probes,
        scan_log: ScanLog,
        store: FindingsStore,
        *,
        docker: Runner,
        cli_path: str,
        python: str = sys.executable,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        poll_s: float = 30.0,
        work_root: Path | None = None,
        claude_config_dir: str | None = None,
        kill_tree: Callable[[subprocess.Popen[str]], None] | None = None,
        org: str = "",
    ) -> None:
        self.config = config
        self.probes = probes
        self.scan_log = scan_log
        self.store = store
        self.docker = docker
        self.cli_path = cli_path
        self.python = python
        self.popen = popen
        self.poll_s = poll_s
        self.work_root = work_root
        self.claude_config_dir = claude_config_dir
        self.kill_tree = kill_tree or watch.kill_tree
        self.org = org
        self.cancelled = threading.Event()

    # ── admission ────────────────────────────────────────────────────────

    def host(self) -> ScanHost | None:
        eligible = [h for h in self.config.scan_hosts if h.eligible_for_real_scans]
        return eligible[0] if eligible else None

    def facts(self, repo: str, host: ScanHost | None) -> Facts:
        contract = self.probes.contract(repo)
        return Facts(
            now=self.probes.now(),
            host=host,
            contract_valid=bool(contract.get("valid")),
            contract_disabled=bool(contract.get("disabled")),
            default_branch_sha=self.probes.default_branch_sha(repo),
            free_gb=self.probes.free_gb(host) if host else None,
            build_filter_ok=self.probes.build_filter_ok(host) if host else False,
            queued_jobs=self.probes.queued_jobs(),
            slot_free=self.scan_log.slot_free(),
            spent_usd_today=self.scan_log.spent_today(),
            oauth_ok=self.probes.oauth_ok(),
        )

    def plan(self, req: ScanRequest, host: ScanHost | None = None) -> list[str]:
        """Every reason ``req`` would be refused; writes nothing (scheduler, #2574)."""
        host = host or self.host()
        reasons = admit(self.config, req.repo, req.sha, self.facts(req.repo, host))
        if not reasons and not req.dry_run:
            reasons = self._cli_problems()
        return reasons

    def submit(self, req: ScanRequest) -> dict[str, Any]:
        """Admit or refuse; an admitted real scan takes the slot (state ``running``).

        The caller then runs :meth:`execute` (normally on a worker thread).
        """
        host = self.host()
        reasons = self.plan(req, host)
        create = functools.partial(
            self.scan_log.create,
            repo=req.repo,
            sha=req.sha,
            host=host.name if host else "",
            dry_run=req.dry_run,
            requested_by=req.requested_by,
        )
        if req.dry_run or reasons:
            state = "planned" if req.dry_run else "refused"
            scan_id = create(state=state, reasons=reasons)
        else:
            try:
                scan_id = create(state="running", reasons=[])
            except SlotBusy as exc:
                scan_id = create(state="refused", reasons=[str(exc)])
        return self.scan_log.get(scan_id) or {"id": scan_id}

    def _cli_problems(self) -> list[str]:
        try:
            launch.check_cli_version(
                self.probes.cli_version(), self.config.executor.cli_version
            )
        except launch.LaunchRefused as exc:
            return [str(exc)]
        return []

    # ── execution ────────────────────────────────────────────────────────

    def execute(self, scan_id: str, req: ScanRequest) -> dict[str, Any]:
        host = self.host()
        if host is None:
            self.scan_log.update(
                scan_id, state="refused", reasons=["no scan host"], finished=_now()
            )
            return self.scan_log.get(scan_id) or {}
        env = docker_env(host)
        container = container_name(scan_id)
        image = image_tag(scan_id, req.sha)
        work = Path(tempfile.mkdtemp(prefix="vs-", dir=self.work_root))
        state, error = "failed", ""
        alive = self._heartbeat(scan_id)
        try:
            context = work / "context"
            sterile = work / "agent"
            context.mkdir()
            sterile.mkdir()
            self._phase(scan_id, "source")
            self._sweep(env)
            self.probes.fetch_source(req.repo, req.sha, context)
            dockerfile = local_contract(self.org, req.repo, context)
            digest = self._build(scan_id, host, env, context, image, dockerfile)
            self.scan_log.update(scan_id, image_digest=digest)
            reasons = admit(self.config, req.repo, req.sha, self.facts(req.repo, host))
            reasons = [r for r in reasons if "slot is busy" not in r]  # we hold it
            if reasons:
                raise ScanAborted("admission recheck failed: " + "; ".join(reasons))
            self._phase(scan_id, "sandbox")
            self._docker(run_argv(container, image), env, 120, "docker run")
            run = self._agent(scan_id, req, container, env, sterile)
            ids, created, seen = ingest_findings(
                self.store,
                sterile / "findings.jsonl",
                repo=req.repo,
                sha=req.sha,
                digest=digest,
                scan_id=scan_id,
                cap=self.config.max_new_findings_per_scan,
            )
            self.scan_log.update(
                scan_id,
                findings_new=created,
                findings_seen=seen,
                cost_usd=run.cost_usd,
                turns=run.turns,
            )
            if ids and self.config.executor.verifier_model:
                (work / "verify").mkdir()
                checked = self._verify(scan_id, req, image, env, work / "verify", ids)
                self.scan_log.update(
                    scan_id,
                    cost_usd=run.cost_usd + checked.cost_usd,
                    turns=run.turns + checked.turns,
                )
            state = "succeeded"
        except ScanAborted as exc:
            state, error = "aborted", str(exc)
        except launch.LaunchRefused as exc:
            state, error = "aborted", f"launch refused: {exc}"
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, then cleaned up
            log.exception("scan %s failed", scan_id)
            state, error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        finally:
            alive.set()
            self._cleanup(
                (container, verify_container(scan_id)), image, env, work, host
            )
            self.scan_log.update(
                scan_id, state=state, error=error, phase="done", finished=_now()
            )
        return self.scan_log.get(scan_id) or {}

    def _phase(self, scan_id: str, phase: str) -> None:
        if self.cancelled.is_set():
            raise ScanAborted("cancelled by operator")
        self.scan_log.update(scan_id, phase=phase)

    def _docker(
        self, argv: list[str], env: Mapping[str, str], timeout: float, what: str
    ) -> str:
        result = self.docker(argv, timeout, 64 * 1024, env)
        if result.exit_code != 0 or result.timed_out:
            raise RuntimeError(
                f"{what} failed ({result.exit_code}): {result.stderr[-500:]}"
            )
        return result.stdout.strip()

    def _build(
        self,
        scan_id: str,
        host: ScanHost,
        env: Mapping[str, str],
        context: Path,
        image: str,
        dockerfile: str,
    ) -> str:
        self._phase(scan_id, "build")
        argv = build_argv(host, context, dockerfile, image)
        limit = self.config.executor.build_timeout_minutes * 60
        self._watched(argv, env, limit, idle_s=limit, on_line=None, stdin_text=None)
        return image_digest(self._docker(inspect_argv(image), env, 60, "inspect"))

    def _sweep(self, env: Mapping[str, str]) -> None:
        """Remove scan containers and images a dead dashboard process left behind.

        Called only while this scan holds the fleet-wide slot, so no live scan
        owns anything carrying the scan label.
        """
        for listing, remove in SWEEPS:
            ids = docker_ids(self._docker(listing, env, 60, "sweep list"))
            if ids:
                log.warning("sweeping %d leftover scan object(s): %s", len(ids), ids)
                self._docker([*remove, *ids], env, 120, "sweep")

    def _heartbeat(self, scan_id: str) -> threading.Event:
        """Beat the scan row until the returned event is set (see ``scans``)."""
        done = threading.Event()

        def beat() -> None:
            while not done.wait(HEARTBEAT_S):
                try:
                    self.scan_log.beat(scan_id)
                except Exception:  # noqa: BLE001 - a missed beat is retried
                    log.warning("scan %s heartbeat failed", scan_id, exc_info=True)

        threading.Thread(target=beat, name=f"scan-beat-{scan_id}", daemon=True).start()
        return done

    def _agent(
        self,
        scan_id: str,
        req: ScanRequest,
        container: str,
        env: Mapping[str, str],
        sterile: Path,
    ) -> AgentStream:
        self._phase(scan_id, "agent")
        settings = self.config.executor
        cap = self.config.max_new_findings_per_scan
        return self._run_agent(
            container,
            env,
            sterile,
            mode="scan",
            model=settings.model,
            max_turns=settings.max_turns,
            prompt=scan_prompt(req.repo, req.sha, cap),
            out=sterile / "findings.jsonl",
            max_findings=cap * 3,
        )

    def _verify(
        self,
        scan_id: str,
        req: ScanRequest,
        image: str,
        env: Mapping[str, str],
        sterile: Path,
        ids: list[str],
    ) -> AgentStream:
        """Re-test this scan's new findings with a second model in a fresh container."""
        self._phase(scan_id, "verify")
        settings = self.config.executor
        model = str(settings.verifier_model)
        rows = [self.store.get(i) for i in ids]
        findings = [f for f in rows if f and f["state"] == "new"]
        if not findings:
            return AgentStream("verify", 0)
        container = verify_container(scan_id)
        self._docker(run_argv(container, image), env, 120, "docker run (verify)")
        out = sterile / "verdicts.jsonl"
        run = self._run_agent(
            container,
            env,
            sterile,
            mode="verify",
            model=model,
            max_turns=settings.verify_max_turns,
            prompt=verify_prompt(req.repo, req.sha, findings),
            out=out,
            max_findings=len(findings),
            finding_ids=tuple(f["id"] for f in findings),
        )
        apply_verdicts(self.store, out, {f["id"] for f in findings}, model)
        return run

    def _run_agent(
        self,
        container: str,
        env: Mapping[str, str],
        sterile: Path,
        *,
        mode: str,
        model: str,
        max_turns: int,
        prompt: str,
        out: Path,
        max_findings: int,
        finding_ids: tuple[str, ...] = (),
    ) -> AgentStream:
        """One sanitized CLI run against one container, in scan or verify mode."""
        settings = self.config.executor
        config = launch.mcp_config(
            python=self.python,
            server_path=SERVER_PATH,
            container=container,
            findings_out=out,
            max_findings=max_findings,
            exec_timeout_s=settings.exec_timeout_s,
            docker_env=env,
            mode=mode,
            finding_ids=finding_ids,
        )
        mcp_path = launch.write_mcp_config(sterile, config)
        argv = launch.cli_argv(
            self.cli_path, mcp_config_path=mcp_path, model=model, mode=mode
        )
        run = AgentStream(mode, max_turns)

        self._watched(
            argv,
            launch.scan_env(
                settings.exec_timeout_s, claude_config_dir=self.claude_config_dir
            ),
            settings.scan_timeout_minutes * 60,
            idle_s=settings.exec_timeout_s + 120,
            on_line=run.on_line,
            stdin_text=prompt,
            cwd=sterile,
        )
        return run.finish()

    def _watched(
        self,
        argv: list[str],
        env: Mapping[str, str],
        timeout_s: float,
        *,
        idle_s: float,
        on_line: Callable[[str], None] | None,
        stdin_text: str | None,
        cwd: Path | None = None,
    ) -> None:
        """Run a process while re-polling the queue/disk (:func:`watch_process`)."""
        watch_process(
            self.popen,
            argv,
            env,
            timeout_s,
            idle_s=idle_s,
            on_line=on_line,
            stdin_text=stdin_text,
            cwd=cwd,
            cancelled=self.cancelled,
            poll_s=self.poll_s,
            poll=self._runtime_check,
            kill=self.kill_tree,
        )

    def _runtime_check(self) -> None:
        host = self.host()
        facts = Facts(
            now=self.probes.now(),
            host=host,
            free_gb=self.probes.free_gb(host) if host else None,
            queued_jobs=self.probes.queued_jobs(),
        )
        problems = runtime_problems(self.config, facts)
        if problems:
            raise ScanAborted("; ".join(problems))

    def _cleanup(
        self,
        containers: tuple[str, ...],
        image: str,
        env: Mapping[str, str],
        work: Path,
        host: ScanHost,
    ) -> None:
        steps = cleanup_steps(containers, image, host)
        for argv in steps:
            try:
                self.docker(argv, 120, 4096, env)
            except Exception as exc:  # noqa: BLE001 - cleanup must try every step
                log.warning("scan cleanup %s failed: %s", argv[1], exc)
        shutil.rmtree(work, ignore_errors=True)
