"""Run Repository_Management scripts as subprocesses — the one helper (issue #1229).

RM code is never imported: every call is ``<STAFF_RM_PYTHON> -m scripts.<name>
...`` with ``cwd`` = the RM checkout located by ``staff.workspace.rm_root``.
Shared by the staff lease ritual (``staff.lease``) and the coordination API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.workspace import rm_root

DEFAULT_TIMEOUT = 120
RM_MISSING = "Repository_Management checkout not found (set STAFF_RM_ROOT)"


@dataclass(frozen=True)
class ScriptResult:
    """Outcome of one RM script call. ``rc == 130`` means it could not run or timed out."""

    rc: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """stdout and stderr combined, for logs and run events."""
        return (self.stdout + self.stderr).strip()

    def json(self) -> dict[str, Any] | None:
        """The JSON object the script printed (whole stdout, else its last JSON line), or None."""
        text = self.stdout.strip()
        candidates = [text, *reversed(text.splitlines())] if text else []
        for chunk in candidates:
            try:
                data = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
        return None

    def failure(self) -> str:
        """Short human reason for a failed call (``error``, else RM's ``errors`` list, else stderr tail, else rc)."""
        data = self.json() or {}
        if data.get("error"):
            return str(data["error"])
        if isinstance(data.get("errors"), list) and data["errors"]:
            return "; ".join(str(e) for e in data["errors"])
        tail = self.output[-300:]
        return tail or f"exit code {self.rc}"


def python_for_rm() -> str:
    return os.environ.get("STAFF_RM_PYTHON") or shutil.which("python3") or shutil.which("python") or "python"


def run_argv(argv: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> ScriptResult:
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)  # noqa: S603
        return ScriptResult(r.returncode, r.stdout or "", r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ScriptResult(130, "", str(exc))


def run_module(module: str, *args: str, root: Path | None = None, timeout: int = DEFAULT_TIMEOUT) -> ScriptResult:
    """Run ``scripts.<module>`` in the RM checkout. Pre: ``root`` or a discoverable checkout."""
    root = root or rm_root()
    if root is None:
        return ScriptResult(127, "", RM_MISSING)
    return run_argv([python_for_rm(), "-m", f"scripts.{module}", *args], cwd=root, timeout=timeout)
