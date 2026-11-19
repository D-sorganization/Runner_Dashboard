"""Durable report-run orchestration and recovery state machine for Runner_Dashboard.

Coordinates bounded LaTeX/DOCX build attempts with:
- Single-writer destination lock to prevent concurrent run clobbering.
- Guarded status read/parse (handling interrupted ``running`` and corrupt JSON).
- Stale-artifact clearing on every attempt and zero approval inheritance.
- Guarded final artifact hashing and atomic persistence with independent error reporting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STEP_TIMEOUT_S = 90.0
_REQUIRED_SOURCE_DIRS = ("scripts", "figures")
_REQUIRED_SOURCE_FILES = ("report.tex", "inentec-report.sty", "inentec_logo.png")
_ARTIFACT_REL_PATHS = ("build/report.pdf", "build/report.docx")


@dataclass(frozen=True)
class CommandResult:
    """Captured outcome of one subprocess build step."""

    returncode: int
    stdout: str
    stderr: str


RunnerFn = Callable[[list[str], float], CommandResult]
ErrorReporterFn = Callable[[dict[str, Any]], None]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write JSON status to ``path`` using a unique sibling temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name,
        suffix=".pending",
        delete=False,
    ) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _default_runner(cmd: list[str], timeout_s: float) -> CommandResult:
    proc = subprocess.run(
        [sys.executable, *cmd],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    return CommandResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _read_prior_status(status_path: Path) -> tuple[dict[str, Any], str | None, str | None]:
    """Read prior job-status.json safely; return (dict, recovered_prior_state, parse_error)."""
    if not status_path.exists():
        return {}, None, None
    try:
        raw = status_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, "corrupt_status", f"{type(exc).__name__}: {exc}"
    if not isinstance(parsed, dict):
        return {}, "corrupt_status", "Prior job-status.json root must be a JSON object"
    if parsed.get("state") == "running":
        return parsed, "interrupted", None
    return parsed, None, None


def _clear_stale_artifacts(destination: Path) -> None:
    for rel in _ARTIFACT_REL_PATHS:
        (destination / rel).unlink(missing_ok=True)


def _persist_or_report(
    status_path: Path,
    result: dict[str, Any],
    error_reporter: ErrorReporterFn | None,
) -> dict[str, Any]:
    """Persist ``result`` atomically; on OSError report independently and force failed state."""
    try:
        save_json(status_path, result)
    except OSError as exc:
        result["state"] = "failed"
        result["approval_ready"] = False
        result["artifacts"] = {}
        result["persistence_failed"] = True
        result["error_type"] = type(exc).__name__
        result["reason"] = f"Status persistence failed: {exc}"
        logger.error("Report-run status persistence failed at %s: %s", status_path, exc)
        if error_reporter is not None:
            try:
                error_reporter(dict(result))
            except Exception as report_exc:  # noqa: BLE001 - reporter must not mask state
                logger.error("Independent error_reporter failed: %s", report_exc)
    return result


def _acquire_lock(lock_path: Path) -> int:
    """Acquire a process-scoped lock that the OS releases after a crash."""
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            win_lock: Any = msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            win_lock.locking(fd, win_lock.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _release_lock(fd: int | None) -> None:
    # Keep the inode stable; unlinking can let two processes lock different files.
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            win_lock: Any = msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            win_lock.locking(fd, win_lock.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def run_report_job(
    source: Path,
    destination: Path,
    case: str,
    *,
    timeout_s: float = DEFAULT_STEP_TIMEOUT_S,
    runner: RunnerFn | None = None,
    error_reporter: ErrorReporterFn | None = None,
) -> dict[str, Any]:
    """Execute one bounded report build attempt with guarded recovery and persistence.

    Preconditions:
      - ``timeout_s`` > 0.
    Postconditions:
      - ``approval_ready`` is always ``False`` (build success never grants release approval).
      - ``artifacts`` is non-empty only when ``state == "awaiting_visual_review"``.
      - Persistence failures set ``persistence_failed=True``, ``state="failed"``,
        and invoke ``error_reporter`` if supplied.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and > 0")

    attempt_id = uuid.uuid4().hex
    status_path = destination / "job-status.json"
    result: dict[str, Any] = {
        "attempt_id": attempt_id,
        "case": case,
        "state": "running",
        "approval_ready": False,
        "commands": [],
        "artifacts": {},
        "persistence_failed": False,
    }

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["state"] = "failed"
        result["persistence_failed"] = True
        result["error_type"] = type(exc).__name__
        result["reason"] = f"Destination mkdir failed: {exc}"
        logger.error("Report-run destination mkdir failed for %s: %s", destination, exc)
        if error_reporter is not None:
            try:
                error_reporter(dict(result))
            except Exception as report_exc:  # noqa: BLE001 - reporter must not mask state
                logger.error("Independent error_reporter failed: %s", report_exc)
        return result

    lock_path = destination / ".report_run.lock"
    lock_fd: int | None = None
    try:
        lock_fd = _acquire_lock(lock_path)
    except OSError as exc:
        result["state"] = "failed"
        result["error_type"] = "ConcurrentRunError"
        result["reason"] = f"Could not acquire report-run lock: {exc}"
        return result

    try:
        _, recovered_state, parse_err = _read_prior_status(status_path)
        if recovered_state is not None:
            result["recovered_prior_state"] = recovered_state
        if parse_err is not None:
            result["prior_status_error"] = parse_err

        _clear_stale_artifacts(destination)
        _persist_or_report(status_path, result, error_reporter)
        if result["persistence_failed"]:
            return result

        if case == "missing_input":
            result["state"] = "blocked"
            result["reason"] = "Required moisture evidence absent"
            return _persist_or_report(status_path, result, error_reporter)

        if case == "unsupported_claim":
            result["state"] = "rejected"
            result["reason"] = "Sizing margin lacks calculation/source support"
            return _persist_or_report(status_path, result, error_reporter)

        run_fn = runner or _default_runner
        try:
            for name in _REQUIRED_SOURCE_DIRS:
                shutil.copytree(source / name, destination / name, dirs_exist_ok=True)
            for name in _REQUIRED_SOURCE_FILES:
                shutil.copy2(source / name, destination / name)

            tex = destination / "report.tex"
            if case == "build_failure":
                tex.write_text(
                    tex.read_text(encoding="utf-8").replace(
                        r"\begin{document}",
                        r"\begin{document}\DefinitelyInvalidQualificationCommand",
                    ),
                    encoding="utf-8",
                )

            steps = (
                [str(destination / "scripts" / "write_values.py")],
                [str(destination / "scripts" / "compile_report.py"), str(tex)],
                [str(destination / "scripts" / "build_docx.py"), str(tex)],
                [str(destination / "scripts" / "build_docx.py"), str(tex), "--check"],
            )
            for cmd in steps:
                proc = run_fn(cmd, timeout_s)
                result["commands"].append(
                    {
                        "args": cmd,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                    }
                )
                if proc.returncode != 0:
                    result["state"] = "failed"
                    result["error_type"] = "BuildCommandError"
                    result["reason"] = f"Command exited with code {proc.returncode}"
                    return _persist_or_report(status_path, result, error_reporter)

            result["artifacts"] = {rel: _sha256(destination / rel) for rel in _ARTIFACT_REL_PATHS}
            result["state"] = "awaiting_visual_review"
        except (subprocess.TimeoutExpired, OSError, shutil.Error) as exc:
            result["state"] = "failed"
            result["artifacts"] = {}
            result["error_type"] = type(exc).__name__
            result["reason"] = str(exc)

        return _persist_or_report(status_path, result, error_reporter)
    finally:
        _release_lock(lock_fd)
