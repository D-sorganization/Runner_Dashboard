"""Readiness probe for runner JOB_STARTED/JOB_COMPLETED hooks (#4046)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Literal

ProbeStatus = Literal["ok", "degraded", "down"]


class RunnerHooksProbe:
    """Verify JOB_STARTED/JOB_COMPLETED hooks are installed and match source (#4046).

    Uses ``runner-hooks-restore --check`` when present. Hosts with no runner
    units report ok; missing restore tooling on a runner host is degraded.
    """

    name = "runner_hooks"

    def __init__(
        self,
        restore_bin: str | Path | None = None,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self._restore_bin = (
            Path(restore_bin)
            if restore_bin is not None
            else Path(os.environ.get("RUNNER_HOOKS_RESTORE_BIN", "/usr/local/bin/runner-hooks-restore"))
        )
        self._cache_ttl = cache_ttl_seconds
        self._cached: tuple[float, ProbeStatus, str | None] | None = None

    async def check(self) -> tuple[ProbeStatus, str | None]:
        if self._cache_ttl > 0 and self._cached is not None:
            ts, status, detail = self._cached
            if (time.monotonic() - ts) < self._cache_ttl:
                return status, detail
        status, detail = await asyncio.to_thread(self._check_sync)
        self._cached = (time.monotonic(), status, detail)
        return status, detail

    def _check_sync(self) -> tuple[ProbeStatus, str | None]:
        if not shutil.which("systemctl"):
            return "degraded", "systemctl unavailable; cannot verify runner hooks"
        try:
            result = subprocess.run(
                ["systemctl", "list-unit-files", "--type=service", "--no-legend"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "degraded", f"runner unit discovery failed: {exc}"
        has_runners = any(
            line.split()[0].startswith("actions.runner.") and line.split()[0].endswith(".service")
            for line in (result.stdout or "").splitlines()
            if line.strip()
        )
        if not has_runners:
            return "ok", None
        if not self._restore_bin.is_file():
            return "degraded", f"{self._restore_bin} missing on runner host"
        try:
            check = subprocess.run(
                [str(self._restore_bin), "--check"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "degraded", f"runner-hooks-restore --check failed to run: {exc}"
        if check.returncode == 0:
            return "ok", None
        detail = (check.stdout or check.stderr or "").strip().splitlines()
        msg = detail[0] if detail else "hook drift or missing wiring"
        return "degraded", msg
