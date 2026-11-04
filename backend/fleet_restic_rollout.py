"""Plan fleet restic rollout to Desk, ControlTower, and Brick (#4012).

Ships configuration and installer hooks only. Default mode is dry-run logging;
``FLEET_RESTIC_ROLLOUT_APPLY=1`` or ``apply=True`` records the install steps an
operator would run after the OGLaptop pilot (#4011). Never invokes restic or SSH.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.fleet_restic_rollout")

DEFAULT_HOSTS = ("desk", "controltower", "brick")
_CONFIG = Path(__file__).resolve().parents[1] / "config" / "fleet_restic_hosts.json"
RunFn = Callable[[list[str]], int]


def load_rollout_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _CONFIG
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    hosts = raw.get("hosts") or []
    if [h.get("id") for h in hosts] != list(DEFAULT_HOSTS):
        msg = "fleet_restic_hosts.json must list desk, controltower, brick in order"
        raise ValueError(msg)
    return raw


def _pilot_ready(cfg: dict[str, Any]) -> tuple[bool, str]:
    env_name = str((cfg.get("pilot") or {}).get("oglaptop_complete_env") or "FLEET_RESTIC_PILOT_COMPLETE")
    val = os.environ.get(env_name, "").strip().lower()
    if val in {"1", "true", "yes"}:
        return True, ""
    return False, f"pilot gate: set {env_name}=1 after OGLaptop restic pilot (#4011)"


def plan_restic_rollout(
    *,
    config_path: Path | None = None,
    apply: bool = False,
    run: RunFn | None = None,
) -> dict[str, Any]:
    """Return rollout report; optional ``run`` hook for tests (simulated install)."""
    cfg = load_rollout_config(config_path)
    ready, pilot_warning = _pilot_ready(cfg)
    runner = run or (lambda _cmd: 0)
    if os.environ.get("FLEET_RESTIC_ROLLOUT_APPLY", "").strip().lower() in {"1", "true", "yes"}:
        apply = True

    would: list[dict[str, str]] = []
    configured = 0
    warnings: list[str] = []
    if not ready:
        warnings.append(pilot_warning)

    for host in cfg.get("hosts") or []:
        host_id = str(host.get("id") or "")
        item = {
            "host": host_id,
            "display_name": str(host.get("display_name") or host_id),
            "restic_repo_env": str(host.get("restic_repo_env") or ""),
            "paths": ",".join(host.get("backup_paths") or []),
        }
        if not ready:
            continue
        would.append(item)
        log.info(
            "%s restic rollout for %s (%s)",
            "would configure" if not apply else "configuring",
            item["display_name"],
            host_id,
        )
        if apply:
            cmd = [
                "install",
                host_id,
                item["restic_repo_env"],
                item["paths"],
            ]
            if runner(cmd) == 0:
                configured += 1

    return {
        "dry_run": not apply,
        "pilot_ready": ready,
        "would_configure": would,
        "configured": configured,
        "warnings": warnings,
    }
