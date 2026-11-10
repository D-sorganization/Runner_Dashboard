"""Barbara /backlog-push relay for barb-worker (#3711 extension). Off by default."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import barb_backlog_push_lib as lib
import barb_panel_relay as panel

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:
    psutil = None

logger = logging.getLogger("barb_backlog_push_relay")

REQUEST_LABEL = lib.REQUEST_LABEL
STARTED, DONE, REFUSED = lib.STARTED, lib.DONE, lib.REFUSED
OUTCOME_LABELS = lib.OUTCOME_LABELS
BacklogPushConfig = lib.BacklogPushConfig
load_config = lib.load_config
parse_backlog_push_request = lib.parse_backlog_push_request
validate_bounds = lib.validate_bounds
hub_resource_refusal = lib.hub_resource_refusal
session_names = lib.session_names
build_launch_plan = lib.build_launch_plan
claude_argv = lib.claude_argv


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": {}, "push_starts": {}, "active": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise panel.StateFileError(f"can't read backlog-push relay state {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise panel.StateFileError(f"backlog-push relay state {path} is not a JSON object")
    for key in ("processed", "push_starts"):
        if not isinstance(data.get(key, {}), dict):
            raise panel.StateFileError(f"backlog-push relay state {path} missing map {key!r}")
    if data.get("active") is not None and not isinstance(data["active"], dict):
        raise panel.StateFileError(f"backlog-push relay state {path} has invalid active block")
    return {
        "processed": data.get("processed", {}),
        "push_starts": data.get("push_starts", {}),
        "active": data.get("active"),
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    panel.save_state(path, state)


def _get_process_start_time_ticks(pid: int) -> int | None:
    """Get the process start time in clock ticks since boot. Returns None if unavailable."""
    if pid <= 0:
        return None
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            raw = proc_stat.read_text(encoding="utf-8", errors="replace")
            fields = raw.rsplit(")", 1)[-1].split()
            return int(fields[19])
        except (OSError, IndexError, ValueError):
            pass
    return None


def _pid_identity_matches(pid: int, recorded_start_time_ticks: int | None, recorded_session: str | None) -> bool:
    """Verify that a pid matches the recorded process identity (start_time_ticks and session).

    Returns True only if:
    - The process is alive (os.kill(pid, 0) succeeds)
    - The start time (in clock ticks) matches the recorded value (if recorded)
    - The session token is provided (legacy pid files without session are treated as not matching)

    This prevents signalling an unrelated process that reused a recycled PID after reboot.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    if recorded_session is None:
        return False

    if recorded_start_time_ticks is not None:
        current_start_time_ticks = _get_process_start_time_ticks(pid)
        if current_start_time_ticks is None or current_start_time_ticks != recorded_start_time_ticks:
            return False

    return True


def _read_pid_record(pidfile: Path) -> tuple[int, int | None, str | None]:
    """Read a pid file and extract pid, start_time_ticks, and session token.

    Supports both legacy format (plain text pid) and enhanced format (JSON).
    Legacy format is assumed to have no identity information.
    """
    if not pidfile.is_file():
        return 0, None, None
    try:
        content = pidfile.read_text(encoding="utf-8").strip()
        if content.startswith("{"):
            data = json.loads(content)
            pid = int(data.get("pid", 0))
            start_time_ticks = data.get("start_time")
            if start_time_ticks is not None:
                start_time_ticks = int(start_time_ticks)
            session = data.get("session")
            return pid, start_time_ticks, session
        else:
            pid = int(content)
            return pid, None, None
    except (ValueError, OSError, json.JSONDecodeError):
        return 0, None, None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _active_sessions(active: dict[str, Any]) -> list[str]:
    sessions = [active.get("supervisor_session"), *(active.get("lane_sessions") or [])]
    return [str(s) for s in sessions if s]


def _worker_exit_code(log_dir: Path, session: str) -> int | None:
    path = log_dir / f"{session}.exit"
    if path.is_file():
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    rcpt = log_dir / f"{session}.receipt"
    if rcpt.is_file():
        try:
            data = json.loads(rcpt.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "exit_code" in data:
                return int(data["exit_code"])
        except (ValueError, TypeError):
            pass
    return None


def _evaluate_active_outcome(
    active: dict[str, Any],
) -> tuple[bool, str, str | None, dict[str, int | None]]:
    """Inspect active push processes and exit evidence.

    Returns:
        (ready, outcome, detail, exit_codes)
        - ready: False if any tracked process is still running.
        - outcome: 'completed', 'failed', or 'unknown'.
        - detail: human-readable failure/unknown summary (None if clean completed).
        - exit_codes: map of session -> exit code (or None).
    """
    log_dir_s = active.get("log_dir")
    base = Path(str(log_dir_s)) if log_dir_s else None
    sessions = _active_sessions(active)

    if base is not None:
        for session in sessions:
            pidfile = base / f"{session}.pid"
            if pidfile.is_file():
                pid, start_time, recorded_session = _read_pid_record(pidfile)
                if pid > 0 and _pid_identity_matches(pid, start_time, recorded_session or session):
                    return False, "running", None, {}
                elif pid > 0 and _pid_alive(pid):
                    logger.warning("session %s: pidfile points to unrelated pid %d; marking lane lost", session, pid)
                    pidfile.unlink(missing_ok=True)

    pids = active.get("pids") if isinstance(active.get("pids"), dict) else {}
    if any(_pid_alive(int(p)) for p in pids.values() if isinstance(p, int)):
        return False, "running", None, {}

    if base is None or not sessions:
        return True, "unknown", "processes terminated without recorded session evidence", {}

    exit_codes: dict[str, int | None] = {}
    for session in sessions:
        exit_codes[session] = _worker_exit_code(base, session)

    missing = [s for s, c in exit_codes.items() if c is None]
    failed = [s for s, c in exit_codes.items() if c is not None and c != 0]

    if failed:
        parts = [f"{s} exited with status {exit_codes[s]}" for s in failed]
        if missing:
            parts.extend(f"{s} exited without recording exit status" for s in missing)
        return True, "failed", "; ".join(parts), exit_codes

    if missing:
        parts = [f"{s} exited without recording exit status" for s in missing]
        return True, "unknown", "; ".join(parts), exit_codes

    return True, "completed", None, exit_codes


def _push_completion(active: dict[str, Any]) -> tuple[bool, str | None]:
    """Return (ready_to_finalize, failure_detail). Not ready while a worker pid is alive."""
    ready, outcome, detail, _ = _evaluate_active_outcome(active)
    if not ready:
        return False, None
    if outcome == "completed":
        return True, None
    return True, detail


def _push_processes_alive(active: dict[str, Any]) -> bool:
    """True if any tracked backlog-push lane pidfile or stored pid is still running."""
    log_dir = active.get("log_dir")
    sessions = [active.get("supervisor_session"), *(active.get("lane_sessions") or [])]
    if log_dir:
        base = Path(str(log_dir))
        for session in sessions:
            if not session:
                continue
            pidfile = base / f"{session}.pid"
            if pidfile.is_file():
                pid, start_time, recorded_session = _read_pid_record(pidfile)
                if pid > 0 and _pid_identity_matches(pid, start_time, recorded_session or session):
                    return True
    pids = active.get("pids") if isinstance(active.get("pids"), dict) else {}
    return any(_pid_alive(int(p)) for p in pids.values() if isinstance(p, int))


def _terminate_tree(pids: Mapping[str, Any]) -> None:
    for key in ("supervisor", *tuple(f"lane{i}" for i in range(1, 9))):
        pid = pids.get(key)
        if isinstance(pid, int) and pid > 0:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except OSError:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass


def _terminate_from_active(active: dict[str, Any]) -> None:
    pids = active.get("pids") if isinstance(active.get("pids"), dict) else {}
    _terminate_tree(pids)
    log_dir = active.get("log_dir")
    if not log_dir:
        return
    base = Path(str(log_dir))
    for session in [active.get("supervisor_session"), *(active.get("lane_sessions") or [])]:
        if not session:
            continue
        pidfile = base / f"{session}.pid"
        if not pidfile.is_file():
            continue
        pid, start_time, recorded_session = _read_pid_record(pidfile)
        if pid <= 0:
            continue
        if not _pid_identity_matches(pid, start_time, recorded_session or session):
            if _pid_alive(pid):
                logger.warning(
                    "session %s: pidfile points to unrelated pid %d; skipping signal and removing pidfile",
                    session,
                    pid,
                )
            pidfile.unlink(missing_ok=True)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


class BacklogPushRelay:
    def __init__(
        self,
        cfg: BacklogPushConfig,
        github: Any,
        state: dict[str, Any],
        *,
        launcher: Callable[[dict[str, Any], Path], dict[str, int]] | None = None,
        today: str | None = None,
        dry_run: bool = False,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.cfg = cfg
        self.github = github
        self.state = state
        self.launcher = launcher or self._default_launcher
        self.today = today or datetime.now(UTC).date().isoformat()
        self.dry_run = dry_run
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.counts = dict.fromkeys(("started", "done", "refused", "skipped", "errors"), 0)

    def run(self) -> dict[str, int]:
        try:
            self._reap_active()
            if lib.single_flight_active(self.state.get("active")):
                return self.counts
            for issue in self.github.open_requests(self.cfg.repo, REQUEST_LABEL):
                self._consider(issue)
                if self.state.get("active"):
                    break
        finally:
            if not self.dry_run:
                save_state(self.cfg.state_path, self.state)
        return self.counts

    def _reap_active(self) -> None:
        active = self.state.get("active")
        if not active:
            return
        number = int(active["issue"])
        deadline = datetime.fromisoformat(str(active["deadline_at"]))
        now = self.now_fn()
        if now >= deadline:
            if self.dry_run:
                logger.info("[dry-run] #%s: would finalize (timeout)", number)
                return
            summary = (
                "Backlog push finished (wall-clock timeout reached).\n"
                f"Supervisor session: `{active.get('supervisor_session')}`.\n"
                f"Lane sessions: {', '.join(f'`{s}`' for s in active.get('lane_sessions') or [])}."
            )
            self._finalize(number, summary, timed_out=True, outcome="timeout")
            return
        ready, outcome, detail, exit_codes = _evaluate_active_outcome(active)
        if not ready:
            return
        if self.dry_run:
            logger.info("[dry-run] #%s: would finalize (workers finished, outcome=%s)", number, outcome)
            return
        if outcome == "completed":
            summary = (
                "Backlog push finished (all lane processes exited cleanly).\n"
                f"Supervisor session: `{active.get('supervisor_session')}`.\n"
                f"Lane sessions: {', '.join(f'`{s}`' for s in active.get('lane_sessions') or [])}."
            )
        elif outcome == "failed":
            summary = (
                f"Backlog push finished with errors ({detail}).\n"
                f"Supervisor session: `{active.get('supervisor_session')}`.\n"
                f"Lane sessions: {', '.join(f'`{s}`' for s in active.get('lane_sessions') or [])}."
            )
        else:
            summary = (
                f"Backlog push finished with unknown outcome ({detail}).\n"
                f"Supervisor session: `{active.get('supervisor_session')}`.\n"
                f"Lane sessions: {', '.join(f'`{s}`' for s in active.get('lane_sessions') or [])}."
            )
        self._finalize(number, summary, timed_out=False, outcome=outcome, exit_codes=exit_codes)

    def _consider(self, issue: dict[str, Any]) -> None:
        number = int(issue["number"])
        labels = {str(lbl.get("name")) for lbl in issue.get("labels", [])}
        if str(number) in self.state["processed"] or labels & OUTCOME_LABELS:
            return
        snap = self.github.snapshot(self.cfg.repo, number)
        if snap["author"] not in self.cfg.allowed_authors:
            self._skip(number, f"author {snap['author']!r} is not allowlisted")
            return
        if snap["editor"] is not None and snap["editor"] not in self.cfg.allowed_authors:
            self._skip(number, f"last editor {snap['editor']!r} is not allowlisted")
            return
        if lib.push_daily_cap_reached(self.state["push_starts"], self.today, self.cfg.daily_cap):
            self._refuse(number, f"daily cap of {self.cfg.daily_cap} push(es)/day reached (resets 00:00 UTC)")
            return
        try:
            params = parse_backlog_push_request(snap["body"])
            validate_bounds(params, self.cfg)
        except ValueError as exc:
            self._refuse(number, str(exc))
            return
        try:
            refusal = hub_resource_refusal(self.cfg.hub_url, self.cfg.token)
        except panel.HubError:
            raise
        if refusal:
            self._refuse(number, refusal)
            return
        supervisor, lane_sessions = session_names(number, int(params["lanes"]), self.now_fn())
        plan = build_launch_plan(params, supervisor, lane_sessions)
        if self.dry_run:
            logger.info(
                "[dry-run] #%s: would start backlog push (%s repos, %s min, %s lanes) sessions=%s",
                number,
                len(params["repos"]),
                params["duration_min"],
                params["lanes"],
                supervisor,
            )
            return
        log_dir = self.cfg.state_path.parent / "backlog-push-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            pids = self.launcher({"plan": plan}, log_dir)
        except RuntimeError as exc:
            self._refuse(number, str(exc))
            return
        started_at = self.now_fn()
        deadline = started_at + timedelta(minutes=int(params["duration_min"]))
        self.state["push_starts"][self.today] = self.state["push_starts"].get(self.today, 0) + 1
        self.state["active"] = {
            "issue": number,
            "started_at": started_at.isoformat(),
            "deadline_at": deadline.isoformat(),
            "supervisor_session": supervisor,
            "lane_sessions": lane_sessions,
            "pids": pids,
            "log_dir": str(log_dir),
            "duration_min": int(params["duration_min"]),
        }
        note = (
            f"Backlog push **accepted** at `{started_at.isoformat()}`.\n"
            f"Supervisor session: `{supervisor}`.\n"
            f"Lane sessions: {', '.join(f'`{s}`' for s in lane_sessions)}.\n"
            f"Wall-clock timeout: `{deadline.isoformat()}`."
        )
        self._mark(number, {"status": "running", "supervisor_session": supervisor}, note, STARTED)
        self.counts["started"] += 1

    def _default_launcher(self, payload: dict[str, Any], log_dir: Path) -> dict[str, int]:
        root = Path(os.environ.get("RUNNER_DASHBOARD_ROOT", "/opt/Runner_Dashboard"))
        spawn_py = root / "scripts/barb-worker/backlog-push-spawn.py"
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
        proc = subprocess.run(
            [sys.executable, str(spawn_py), str(log_dir)],
            input=json.dumps(payload["plan"]),
            text=True,
            capture_output=True,
            env=env,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            logger.error("backlog-push spawn failed: %s", (proc.stderr or proc.stdout or "").strip())
            raise RuntimeError("backlog-push spawn failed")
        return json.loads(proc.stdout or "{}")

    def _finalize(
        self,
        number: int,
        summary: str,
        *,
        timed_out: bool,
        outcome: str = "completed",
        exit_codes: dict[str, int | None] | None = None,
    ) -> None:
        if self.dry_run:
            return
        active = self.state.get("active") or {}
        if timed_out:
            _terminate_from_active(active)
        log_dir_s = active.get("log_dir")
        if log_dir_s:
            try:
                receipt_path = Path(str(log_dir_s)) / "outcome_receipt.json"
                tmp_receipt = receipt_path.with_suffix(".tmp")
                receipt_data = {
                    "issue": number,
                    "outcome": outcome,
                    "exit_codes": exit_codes if exit_codes is not None else {},
                    "finalized_at": self.now_fn().isoformat(),
                    "supervisor_session": active.get("supervisor_session"),
                    "lane_sessions": active.get("lane_sessions") or [],
                }
                tmp_receipt.write_text(json.dumps(receipt_data, indent=2, sort_keys=True), encoding="utf-8")
                tmp_receipt.replace(receipt_path)
            except OSError as exc:
                logger.warning("failed to write outcome receipt: %s", exc)
        self.state["active"] = None
        entry: dict[str, Any] = {
            "status": "done",
            "outcome": outcome,
            "exit_codes": exit_codes if exit_codes is not None else {},
            "supervisor_session": active.get("supervisor_session"),
            "lane_sessions": active.get("lane_sessions") or [],
        }
        self._mark(number, entry, summary, DONE)
        self.counts["done"] += 1

    def _skip(self, number: int, why: str) -> None:
        logger.info("skip #%s: %s", number, why)
        self.counts["skipped"] += 1

    def _refuse(self, number: int, reason: str) -> None:
        body = f"Backlog push **refused**: {reason}."
        self._mark(number, {"status": "refused"}, body, REFUSED)
        self.counts["refused"] += 1

    def _mark(self, number: int, entry: dict[str, Any], body: str, label: str) -> None:
        if self.dry_run:
            logger.info("[dry-run] #%s: would label %s", number, label)
            return
        self.state["processed"][str(number)] = {"day": self.today, **entry}
        save_state(self.cfg.state_path, self.state)
        self.github.comment(self.cfg.repo, number, body)
        self.github.label(self.cfg.repo, number, label)


class GhCliExtended(panel.GhCli):
    def open_requests(self, repo: str, label: str) -> list[dict[str, Any]]:
        issues = self._api("-X", "GET", f"repos/{repo}/issues", "-f", f"labels={label}", "-f", "state=open")
        return [i for i in issues or [] if "pull_request" not in i]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Barbara /backlog-push relay (one pass).")
    parser.add_argument("--dry-run", action="store_true", help="Log the plan; write nothing to GitHub or state.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    if not cfg.enabled and not args.dry_run:
        logger.info("backlog-push relay is off (set RELAY_BACKLOG_PUSH_ENABLED=1 to enable)")
        return 0
    if not cfg.allowed_authors:
        logger.warning("BARB_PANEL_RELAY_AUTHORS is empty, so every request will be skipped")
    try:
        state = load_state(cfg.state_path)
    except panel.StateFileError as exc:
        logger.error("backlog-push relay stopped: %s", exc)
        return 2
    try:
        counts = BacklogPushRelay(cfg, GhCliExtended(), state, dry_run=args.dry_run).run()
    except panel.HubError as exc:
        logger.error("backlog-push relay stopped: hub refused token (HTTP %s)", exc.status)
        return 3
    logger.info("backlog-push relay pass: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
