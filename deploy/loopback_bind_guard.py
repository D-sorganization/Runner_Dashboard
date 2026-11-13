#!/usr/bin/env python3
"""Loopback-auth bind guard helpers for fleet env files and systemd (#3617, #3744).

When ``DASHBOARD_LOOPBACK_AUTH=1``, the dashboard must bind a loopback address.
Operators sometimes omit ``DASHBOARD_HOST`` (implicit 0.0.0.0) or set an
all-interfaces bind in a drop-in — both crash at startup after the bind guard.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LOOPBACK_AUTH_ENV = "DASHBOARD_LOOPBACK_AUTH"
HOST_ENV = "DASHBOARD_HOST"
DEFAULT_LOOPBACK_HOST = "127.0.0.1"
ALL_INTERFACE_HOSTS = frozenset({"0.0.0.0", "::"})

STARTUP_ERROR_SNIPPET = (
    "DASHBOARD_LOOPBACK_AUTH=1 cannot start with DASHBOARD_HOST binding all interfaces"
)

DROPIN_NAME = "99-loopback-host.conf"
DROPIN_BODY = """[Service]
Environment=DASHBOARD_HOST=127.0.0.1
"""


@dataclass(frozen=True)
class EnvBindIssue:
    reason: str
    host_value: str | None


def _truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def diagnose_env_mapping(env: dict[str, str]) -> EnvBindIssue | None:
    if not _truthy_env(env.get(LOOPBACK_AUTH_ENV)):
        return None
    host_raw = env.get(HOST_ENV)
    if host_raw is None:
        return EnvBindIssue(reason="loopback_auth_missing_host", host_value=None)
    host = host_raw.strip()
    if not host or host in ALL_INTERFACE_HOSTS:
        return EnvBindIssue(reason="loopback_auth_all_interfaces", host_value=host or None)
    try:
        if not ipaddress.ip_address(host).is_loopback:
            return EnvBindIssue(reason="loopback_auth_non_loopback", host_value=host)
    except ValueError:
        return EnvBindIssue(reason="loopback_auth_invalid_host", host_value=host)
    return None


def diagnose_env_file(path: Path) -> EnvBindIssue | None:
    if not path.is_file():
        return None
    return diagnose_env_mapping(parse_env_lines(path.read_text(encoding="utf-8")))


def upsert_env_var(path: Path, key: str, value: str) -> bool:
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    filtered = [ln for ln in lines if not ln.strip().startswith(prefix)]
    filtered.append(f"{key}={value}")
    new_text = "\n".join(filtered) + "\n"
    old_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if new_text == old_text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return True


def ensure_env_loopback_host(path: Path) -> tuple[bool, EnvBindIssue | None]:
    issue = diagnose_env_file(path)
    if issue is None:
        return False, None
    changed = upsert_env_var(path, HOST_ENV, DEFAULT_LOOPBACK_HOST)
    return changed, issue


def systemd_dropin_dir(scope: str, home: Path) -> Path:
    if scope == "user":
        return home / ".config/systemd/user/runner-dashboard.service.d"
    return Path("/etc/systemd/system/runner-dashboard.service.d")


def install_systemd_dropin(scope: str, home: Path) -> bool:
    dest_dir = systemd_dropin_dir(scope, home)
    dest = dest_dir / DROPIN_NAME
    if dest.is_file() and dest.read_text(encoding="utf-8") == DROPIN_BODY:
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(DROPIN_BODY, encoding="utf-8")
    return True


def journal_shows_loopback_bind_failure(service: str = "runner-dashboard.service") -> bool:
    try:
        proc = subprocess.run(
            [
                "journalctl",
                "-u",
                service,
                "-n",
                "40",
                "--no-pager",
                "-o",
                "cat",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return STARTUP_ERROR_SNIPPET in proc.stdout


def repair_loopback_bind(
    env_file: Path,
    *,
    scope: str,
    home: Path,
    apply_dropin: bool = True,
    force_dropin: bool = False,
) -> dict[str, object]:
    actions: list[str] = []
    changed_env, issue = ensure_env_loopback_host(env_file)
    if changed_env:
        actions.append("env_set_dashboard_host_loopback")
    if apply_dropin and (issue is not None or force_dropin):
        if install_systemd_dropin(scope, home):
            actions.append(f"installed_systemd_dropin:{DROPIN_NAME}")
        try:
            if scope == "user":
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"],
                    check=False,
                    timeout=15,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["sudo", "-n", "systemctl", "daemon-reload"],
                    check=False,
                    timeout=15,
                    capture_output=True,
                )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"actions": actions, "issue": issue.reason if issue else None}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_env_file_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--env-file",
            default=str(Path.home() / ".config/runner-dashboard/env"),
        )

    diag = sub.add_parser("diagnose", help="Print JSON issue or null")
    add_env_file_arg(diag)

    ensure_p = sub.add_parser("ensure-env", help="Ensure env file has loopback DASHBOARD_HOST")
    add_env_file_arg(ensure_p)
    ensure_p.add_argument("--quiet", action="store_true")

    repair_p = sub.add_parser("repair", help="Ensure env + optional systemd drop-in")
    add_env_file_arg(repair_p)
    repair_p.add_argument("--scope", choices=("system", "user"), default="system")
    repair_p.add_argument("--home", default=str(Path.home()))
    repair_p.add_argument("--no-dropin", action="store_true")
    repair_p.add_argument(
        "--force-dropin",
        action="store_true",
        help="Install loopback drop-in even when env file looks correct (systemd override)",
    )

    detect_p = sub.add_parser("detect-journal", help="Exit 0 if journal shows bind-guard crash")
    detect_p.add_argument("--service", default="runner-dashboard.service")

    args = parser.parse_args(argv)
    env_path = Path(getattr(args, "env_file", Path.home() / ".config/runner-dashboard/env")).expanduser()

    if args.cmd == "diagnose":
        issue = diagnose_env_file(env_path)
        print(json.dumps(issue.__dict__ if issue else None))
        return 0

    if args.cmd == "ensure-env":
        changed, issue = ensure_env_loopback_host(env_path)
        if not args.quiet:
            print(json.dumps({"changed": changed, "issue": issue.reason if issue else None}))
        return 0

    if args.cmd == "repair":
        result = repair_loopback_bind(
            env_path,
            scope=args.scope,
            home=Path(args.home).expanduser(),
            apply_dropin=not args.no_dropin,
            force_dropin=args.force_dropin,
        )
        print(json.dumps(result))
        return 0

    if args.cmd == "detect-journal":
        return 0 if journal_shows_loopback_bind_failure(args.service) else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
