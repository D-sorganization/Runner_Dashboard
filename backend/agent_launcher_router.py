"""HTTP control surface for the cline_agent_launcher.

The launcher itself ships in the sibling repo Repository_Management at
``launchers/cline_agent_launcher/``. This module is the dashboard's
boundary: every request is validated against typed pydantic models, every
side-effect is performed by shelling out to the launcher's CLI (no Python
imports across the sibling-repo boundary, per the runner-dashboard
sibling-repo rule in CLAUDE.md).

Endpoints under ``/api/agent-launcher``:

  GET  /status           live scheduler PID + last runs per agent (file read)
  GET  /config           current user config (validated, normalized)
  PUT  /config           replace user config (validated; rejects on error)
  GET  /repos            live D-org inventory from WSL (subprocess)
  POST /start            start the scheduler (subprocess; no-op if running)
  POST /stop             stop a running scheduler (subprocess)
  POST /run-once         spawn one window for one agent now

Why subprocess instead of import: the launcher lives in another repo, and
runner-dashboard CLAUDE.md forbids cross-repo runtime imports. Subprocess
preserves the boundary and means dashboard restarts do not affect a
running scheduler. Trade-off: ~200ms per call (acceptable for a control
panel that fires on user clicks, not on every request).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger("dashboard.agent_launcher")
router = APIRouter(prefix="/api/agent-launcher", tags=["agent-launcher"])


# ---------------------------------------------------------------------------
# Locating the launcher CLI on disk
# ---------------------------------------------------------------------------
def _launcher_root() -> Path | None:
    """Find ``launchers/cline_agent_launcher/`` in the sibling repo.

    Search order (first hit wins):
      1. $CLINE_LAUNCHER_ROOT env var (operator override).
      2. Common relative locations from this dashboard's repo:
           ../Repository_Management/launchers/cline_agent_launcher
           ../../Repository_Management/launchers/cline_agent_launcher
      3. Common absolute locations on the dev box:
           %USERPROFILE%\\Repositories\\Repository_Management\\launchers\\...
           ~/Repositories/Repository_Management/launchers/...

    Returns None if not found — the endpoints return 503 in that case so the
    UI can show a "launcher not installed" state without crashing.
    """
    override = os.environ.get("CLINE_LAUNCHER_ROOT")
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None

    here = Path(__file__).resolve().parent
    candidates: list[Path] = []
    for parent in (here.parent, here.parent.parent, here.parent.parent.parent):
        candidates.append(
            parent.parent
            / "Repository_Management"
            / "launchers"
            / "cline_agent_launcher"
        )
    home = Path.home()
    candidates += [
        home
        / "Repositories"
        / "Repository_Management"
        / "launchers"
        / "cline_agent_launcher",
        Path("/mnt/c/Users")
        / os.environ.get("USERNAME", "")
        / "Repositories"
        / "Repository_Management"
        / "launchers"
        / "cline_agent_launcher",
    ]
    for c in candidates:
        if c.is_dir() and (c / "bin" / "agent_launcher.py").is_file():
            return c
    return None


def _runtime_root() -> Path:
    """Mirror the launcher's _runtime_root() — same per-user state dir."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "cline_agent_launcher"
    return Path.home() / ".cline_agent_launcher"


def _launcher_python() -> str:
    """Pick a Python interpreter to run the launcher CLI with.

    Honors $CLINE_LAUNCHER_PYTHON, otherwise uses ``python`` on Windows and
    ``python3`` elsewhere. The launcher is std-lib only, so any 3.11+
    interpreter works.
    """
    return os.environ.get(
        "CLINE_LAUNCHER_PYTHON",
        "python" if platform.system() == "Windows" else "python3",
    )


def _require_launcher() -> Path:
    root = _launcher_root()
    if root is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "cline_agent_launcher not found. Set CLINE_LAUNCHER_ROOT "
                "or install Repository_Management as a sibling of this repo."
            ),
        )
    return root


def _run_cli(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run ``agent_launcher.py *args``; return (rc, stdout, stderr).

    Pre: launcher root is resolvable.
    Post: never raises; returns the subprocess result tuple. Caller decides
    how to react to non-zero rc.
    """
    root = _require_launcher()
    cli = root / "bin" / "agent_launcher.py"
    cmd = [_launcher_python(), str(cli), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("launcher subprocess failed: %s", exc)
        return 130, "", str(exc)


# ---------------------------------------------------------------------------
# Pydantic models — DbC at the HTTP boundary
# ---------------------------------------------------------------------------
class AgentStatus(BaseModel):
    name: str
    enabled: bool
    interval_seconds: int
    last_run_iso: str | None
    last_repo: str | None
    last_window_pid: int | None
    lock_alive: bool


class StatusResponse(BaseModel):
    runtime_root: str
    scheduler_running: bool
    scheduler_pid: int | None
    scheduler_started_iso: str | None
    agents: list[AgentStatus]


class RepoEntry(BaseModel):
    name: str
    wsl_path: str
    org: str
    remote_url: str


class ReposResponse(BaseModel):
    wsl_distro: str
    repos_root: str
    org_filter: str
    count: int
    repos: list[RepoEntry]


class RunOnceRequest(BaseModel):
    agent: str = Field(..., min_length=1, max_length=64)


class SimpleResponse(BaseModel):
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Status — file-based, no subprocess needed (cheap polling)
# ---------------------------------------------------------------------------
def _read_state() -> dict:
    p = _runtime_root() / "state.json"
    if not p.is_file():
        return {"agents": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"agents": {}}


def _read_pidfile() -> dict | None:
    p = _runtime_root() / "scheduler.pid"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in r.stdout
        except (subprocess.SubprocessError, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _read_lock(agent_name: str) -> dict | None:
    p = _runtime_root() / "locks" / f"{agent_name}.lock"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Quick status read. Pure file I/O — no subprocess. Safe to poll
    every few seconds from the dashboard."""
    cfg = _read_config_dict_safe()
    state = _read_state()
    pid_info = _read_pidfile()
    scheduler_alive = bool(pid_info and _is_pid_alive(int(pid_info.get("pid", -1))))

    agents = []
    for name, agent_cfg in cfg.get("agents", {}).items():
        runs = state.get("agents", {}).get(name, {}).get("runs", [])
        last = runs[-1] if runs else {}
        lock = _read_lock(name) or {}
        agents.append(
            AgentStatus(
                name=name,
                enabled=bool(agent_cfg.get("enabled", True)),
                interval_seconds=int(agent_cfg.get("interval_seconds", 3600)),
                last_run_iso=last.get("started_iso"),
                last_repo=last.get("repo"),
                last_window_pid=last.get("window_pid"),
                lock_alive=bool(lock and _is_pid_alive(int(lock.get("pid", -1)))),
            )
        )
    return StatusResponse(
        runtime_root=str(_runtime_root()),
        scheduler_running=scheduler_alive,
        scheduler_pid=int(pid_info["pid"]) if pid_info else None,
        scheduler_started_iso=(pid_info or {}).get("started_iso"),
        agents=agents,
    )


def _read_config_dict_safe() -> dict:
    """Read user config without imposing schema validation — used by status
    so a broken config doesn't blank out the page."""
    p = _runtime_root() / "config.json"
    if not p.is_file():
        return {"agents": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"agents": {}}


# ---------------------------------------------------------------------------
# Config — read returns normalized v2; write validates via launcher CLI
# ---------------------------------------------------------------------------
@router.get("/config")
def get_config() -> dict:
    """Return the normalized v2 user config. Delegates to the launcher's
    ``--validate-config`` so the response matches whatever the scheduler
    will see."""
    rc, stdout, stderr = _run_cli("--validate-config")
    if rc != 0:
        raise HTTPException(
            status_code=500,
            detail=f"launcher --validate-config failed: {stderr.strip()[:300]}",
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail=f"launcher returned invalid JSON: {exc}"
        ) from exc


@router.put("/config", response_model=SimpleResponse)
async def put_config(request: Request) -> SimpleResponse:
    """Replace the user config. Validates by writing to a temp file and
    invoking the launcher's ``--validate-config --config <tmp>``; only
    promotes to the real config path if validation passes (atomic
    rename)."""
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object")

    runtime = _runtime_root()
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "config.json"
    tmp = runtime / "config.json.next"

    tmp.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    rc, _, stderr = _run_cli("--validate-config", "--config", str(tmp))
    if rc != 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=422,
            detail=f"config rejected: {stderr.strip().splitlines()[-1] if stderr.strip() else 'see launcher logs'}",
        )

    # Atomic rename — preserves the previous config until we know the new
    # one validates.
    os.replace(tmp, target)
    log.info("agent_launcher config updated via dashboard")
    return SimpleResponse(ok=True, detail=f"config saved to {target}")


# ---------------------------------------------------------------------------
# Repos — live WSL inventory
# ---------------------------------------------------------------------------
@router.get("/repos", response_model=ReposResponse)
def get_repos() -> ReposResponse:
    rc, stdout, stderr = _run_cli("--list-repos", timeout=45.0)
    if rc != 0:
        raise HTTPException(
            status_code=502,
            detail=f"launcher --list-repos failed: {stderr.strip()[:300]}",
        )
    try:
        d = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail=f"launcher returned invalid JSON: {exc}"
        ) from exc
    return ReposResponse(**d)


# ---------------------------------------------------------------------------
# Start / stop / run-once
# ---------------------------------------------------------------------------
def _bat_path() -> Path:
    """The .bat shim is the right entry point on Windows — it knows how to
    detach via pythonw. On non-Windows we fall back to invoking the .py
    directly (the dashboard runs on Windows in production, but tests + dev
    on macOS/Linux should still work)."""
    root = _require_launcher()
    return root / "bin" / "launcher.bat"


@router.post("/start", response_model=SimpleResponse)
def start_scheduler() -> SimpleResponse:
    pid_info = _read_pidfile()
    if pid_info and _is_pid_alive(int(pid_info.get("pid", -1))):
        return SimpleResponse(
            ok=True, detail=f"already running (pid {pid_info['pid']})"
        )

    if platform.system() == "Windows":
        bat = _bat_path()
        if not bat.is_file():
            raise HTTPException(
                status_code=500, detail=f"missing launcher.bat at {bat}"
            )
        try:
            # /B = no new window. pythonw inside the .bat handles detach.
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "/B", str(bat), "--background"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=500, detail=f"spawn failed: {exc}") from exc
    else:
        rc, _, stderr = _run_cli("&")
        if rc != 0:
            raise HTTPException(
                status_code=500, detail=f"launcher exited: {stderr[:300]}"
            )
    return SimpleResponse(ok=True, detail="scheduler start requested")


@router.post("/stop", response_model=SimpleResponse)
def stop_scheduler() -> SimpleResponse:
    rc, stdout, stderr = _run_cli("--stop")
    if rc == 4:
        return SimpleResponse(ok=False, detail="no running scheduler found")
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"--stop failed: {stderr[:300]}")
    return SimpleResponse(ok=True, detail=stdout.strip() or "stop requested")


@router.post("/run-once", response_model=SimpleResponse)
def run_once(req: RunOnceRequest) -> SimpleResponse:
    """Spawn one window for one agent now (does not affect the scheduler)."""
    # Validate the agent exists first by reading the (validated) config.
    cfg = get_config()
    if req.agent not in cfg.get("agents", {}):
        known = list(cfg.get("agents", {}))
        raise HTTPException(
            status_code=404,
            detail=f"unknown agent {req.agent!r}; known: {known}",
        )
    rc, stdout, stderr = _run_cli("--once", req.agent, timeout=90.0)
    if rc == 0:
        return SimpleResponse(ok=True, detail=f"spawned window for {req.agent}")
    raise HTTPException(
        status_code=500,
        detail=f"--once failed (rc={rc}): {(stderr or stdout)[:300]}",
    )


# Defensive: if anyone tries to import shlex in code that gets stripped by a
# linter, keep it referenced. (Used in older drafts; left harmless to avoid
# noqa pollution.)
