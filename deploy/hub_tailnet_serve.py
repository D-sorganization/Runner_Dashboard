#!/usr/bin/env python3
"""Ensure loopback-bound dashboards stay reachable on the tailnet via ``tailscale serve``.

When ``DASHBOARD_LOOPBACK_AUTH=1``, uvicorn binds loopback only (#3617, #3744).
Fleet peers reach each hub/node through registry ``dashboard_url`` values on the
tailnet — not through a wildcard bind. WSL-mirrored hosts use
``wsl-mirrored-port-helper.sh``; native Linux hosts need a local ``tailscale
serve`` bridge to ``http://127.0.0.1:<port>`` (never ``0.0.0.0``).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_DEPLOY_DIR = Path(__file__).resolve().parent
if str(_DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_DIR))

from loopback_bind_guard import (  # noqa: E402
    DEFAULT_LOOPBACK_HOST,
    HOST_ENV,
    LOOPBACK_AUTH_ENV,
    diagnose_env_mapping,
    parse_env_lines,
)

_PORT_IN_STATUS = re.compile(r":(\d+)\b")
_LOOPBACK_UPSTREAM = re.compile(r"127\.0\.0\.1:(\d+)")


@dataclass(frozen=True)
class ServePlan:
    port: int
    upstream: str


def _truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_wsl_environment() -> bool:
    if Path("/proc/version").is_file():
        try:
            text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "microsoft" in text.lower() or "wsl" in text.lower()
    return False


def effective_dashboard_host(env: dict[str, str]) -> str:
    raw = env.get(HOST_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_LOOPBACK_HOST
    return str(raw).strip()


def should_manage_tailnet_serve(env: dict[str, str], *, assume_wsl: bool | None = None) -> tuple[bool, str]:
    if not _truthy_env(env.get(LOOPBACK_AUTH_ENV)):
        return False, "loopback_auth_disabled"
    wsl = is_wsl_environment() if assume_wsl is None else assume_wsl
    if wsl:
        return False, "wsl_uses_mirrored_port_helper"
    host = effective_dashboard_host(env)
    if host in {"0.0.0.0", "::"}:
        return False, "refusing_non_loopback_bind_host"
    if diagnose_env_mapping(env) is not None and env.get(HOST_ENV):
        return False, "env_bind_misconfigured"
    if host not in {DEFAULT_LOOPBACK_HOST, "::1", "localhost"}:
        try:
            import ipaddress

            if not ipaddress.ip_address(host).is_loopback:
                return False, "dashboard_host_not_loopback"
        except ValueError:
            return False, "dashboard_host_invalid"
    return True, "loopback_auth_loopback_bind"


def serve_plan(port: int) -> ServePlan:
    return ServePlan(port=port, upstream=f"http://127.0.0.1:{port}")


def tailscale_binary() -> str | None:
    return shutil.which("tailscale")


def tailscale_serve_status(tailscale: str) -> str:
    try:
        proc = subprocess.run(
            [tailscale, "serve", "status"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return proc.stdout or proc.stderr or ""
    return proc.stdout or ""


def port_listening(port: int) -> bool:
    try:
        proc = subprocess.run(
            ["ss", "-tln", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "LISTEN" in proc.stdout


def wait_for_local_bind(port: int, *, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if port_listening(port):
            return True
        time.sleep(1.0)
    return False


def status_covers_port(status: str, port: int) -> bool:
    return f":{port}" in status


def status_proxies_loopback_upstream(status: str, port: int) -> bool:
    if not status_covers_port(status, port):
        return False
    for line in status.splitlines():
        if f":{port}" not in line:
            continue
        if "127.0.0.1" in line and str(port) in line:
            return True
        if _LOOPBACK_UPSTREAM.search(line) and _LOOPBACK_UPSTREAM.search(line).group(1) == str(port):  # type: ignore[union-attr]
            return True
        if "proxy http://127.0.0.1" in line and str(port) in line:
            return True
    return "127.0.0.1" in status and str(port) in status


def diagnose(env: dict[str, str], port: int, *, assume_wsl: bool | None = None) -> dict[str, object]:
    manage, reason = should_manage_tailnet_serve(env, assume_wsl=assume_wsl)
    plan = serve_plan(port)
    out: dict[str, object] = {
        "manage": manage,
        "reason": reason,
        "port": port,
        "upstream": plan.upstream,
        "local_listener": port_listening(port),
    }
    if not manage:
        return out
    ts = tailscale_binary()
    if not ts:
        out["serve"] = "tailscale_missing"
        return out
    status = tailscale_serve_status(ts)
    if status_proxies_loopback_upstream(status, port):
        out["serve"] = "ok"
    elif status_covers_port(status, port):
        out["serve"] = "port_served_wrong_upstream"
    else:
        out["serve"] = "missing"
    return out


def ensure(
    env: dict[str, str],
    port: int,
    *,
    dry_run: bool = False,
    assume_wsl: bool | None = None,
    wait_seconds: float = 30.0,
) -> dict[str, object]:
    manage, reason = should_manage_tailnet_serve(env, assume_wsl=assume_wsl)
    if not manage:
        return {"changed": False, "action": "skipped", "reason": reason}
    ts = tailscale_binary()
    if not ts:
        return {"changed": False, "action": "skipped", "reason": "tailscale_missing"}
    if not wait_for_local_bind(port, timeout_seconds=wait_seconds):
        return {"changed": False, "action": "failed", "reason": "local_listener_timeout"}
    status = tailscale_serve_status(ts)
    if status_proxies_loopback_upstream(status, port):
        return {"changed": False, "action": "unchanged", "reason": "serve_ok"}
    plan = serve_plan(port)
    cmd = [ts, "serve", "--http", str(port), "--bg", "--yes", plan.upstream]
    if dry_run:
        return {"changed": False, "action": "would_configure", "command": cmd}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"changed": False, "action": "failed", "reason": str(exc)[:200]}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return {"changed": False, "action": "failed", "reason": detail or "tailscale_serve_failed"}
    return {"changed": True, "action": "configured", "upstream": plan.upstream}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_env_lines(path.read_text(encoding="utf-8"))


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--env-file", default=str(Path.home() / ".config/runner-dashboard/env"))
        p.add_argument("--port", type=int, default=8321)
        p.add_argument("--assume-wsl", action="store_true", help="Test hook: treat host as WSL.")

    p_diag = sub.add_parser("diagnose")
    add_common(p_diag)

    p_ensure = sub.add_parser("ensure")
    add_common(p_ensure)
    p_ensure.add_argument("--dry-run", action="store_true")
    p_ensure.add_argument("--wait-seconds", type=float, default=30.0)

    args = parser.parse_args(argv)
    env_path = Path(args.env_file)
    env = _load_env_file(env_path)
    assume_wsl = True if args.assume_wsl else None

    if args.cmd == "diagnose":
        sys.stdout.write(json.dumps(diagnose(env, args.port, assume_wsl=assume_wsl)) + "\n")
        return 0
    if args.cmd == "ensure":
        sys.stdout.write(
            json.dumps(
                ensure(
                    env,
                    args.port,
                    dry_run=args.dry_run,
                    assume_wsl=assume_wsl,
                    wait_seconds=args.wait_seconds,
                )
            )
            + "\n"
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
