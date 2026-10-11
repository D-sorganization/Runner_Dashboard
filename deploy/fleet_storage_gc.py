#!/usr/bin/env python3
"""Roll the canonical storage GC out to every fleet host over Tailscale (#1925).

Inventory comes from two committed sources, never from a hand-typed host list:

* ``backend/machine_registry.yml`` — every live (non-retired) runner pool that
  names a WSL distro gets the ``runner`` profile;
* ``config/storage-gc-workstations.json`` — native-Linux boxes that run no CI
  get the ``workstation`` profile (no /tmp reaping, no container prune).

Each host receives the same payload — ``install-storage-gc.sh`` plus
``runner-cleanup.sh`` as a base64 tarball on stdin — and runs the installer,
which ends with its own ``--verify``. This script only transports and reports;
every rule about what "installed correctly" means lives in the installer.

Exit codes: 0 every selected host verified ok, 1 at least one host did not,
2 usage error (unknown host filter, empty selection).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import platform
import re
import shlex
import subprocess
import sys
import tarfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parent
DEFAULT_WORKSTATIONS = REPO_ROOT / "config" / "storage-gc-workstations.json"
PAYLOAD_FILES = ("install-storage-gc.sh", "runner-cleanup.sh")
PROFILES = ("runner", "workstation")
DEFAULT_RUNNER_USER = "dieterolson"
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")
HOST_TIMEOUT_S = 1800

# Exit-code contract of the bootstrap/installer, mapped to report statuses.
RC_STATUS = {2: "precondition", 3: "needs-sudo", 124: "timeout", 255: "unreachable"}
SUCCESS_STATUSES = frozenset({"ok", "dry-run"})

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

log = logging.getLogger("fleet_storage_gc")

Runner = Callable[[list[str], str], tuple[int, str]]


@dataclass(frozen=True)
class Host:
    """One install target. Validated on construction so nothing unsafe reaches a shell."""

    name: str
    ssh: str
    distro: str | None
    runner_user: str
    runner_roots: tuple[str, ...]
    profile: str

    def __post_init__(self) -> None:
        if not self.ssh or not _TOKEN.match(self.ssh):
            raise ValueError(f"{self.name}: ssh alias {self.ssh!r} must be a plain host token")
        if self.distro is not None and not _TOKEN.match(self.distro):
            raise ValueError(f"{self.name}: distro {self.distro!r} must be a plain token")
        if self.profile not in PROFILES:
            raise ValueError(f"{self.name}: profile {self.profile!r} not in {PROFILES}")
        if not self.runner_user or not _USER.match(self.runner_user):
            raise ValueError(f"{self.name}: runner_user {self.runner_user!r} is not a valid user name")
        if not self.runner_roots:
            raise ValueError(f"{self.name}: runner_roots is empty")
        for root in self.runner_roots:
            if ":" in root:
                raise ValueError(f"{self.name}: runner root {root!r} contains ':' (the RUNNER_ROOTS separator)")
            if not root.startswith("/"):
                raise ValueError(f"{self.name}: runner root {root!r} must be absolute")


@dataclass(frozen=True)
class HostReport:
    host: str
    status: str
    failed: tuple[str, ...]
    output: str


# --- inventory ------------------------------------------------------------------


def _default_roots(user: str) -> tuple[str, ...]:
    return (f"/home/{user}/actions-runners",)


def _preferred_node(machine: dict[str, Any]) -> str:
    nodes = machine.get("tailscale_nodes") or []
    for node in nodes:
        if node.get("preferred"):
            return str(node["name"])
    return str(nodes[0]["name"]) if nodes else str(machine["name"]).lower()


def hosts_from_registry(registry: dict[str, Any], *, runner_user: str) -> list[Host]:
    """Live runner pools (or pool-less machines) that run in a named WSL distro."""
    hosts: list[Host] = []
    for machine in registry.get("machines", []):
        ssh = _preferred_node(machine)
        pools = [p for p in machine.get("runner_pools") or [] if not p.get("retired")]
        targets = pools or [{"name": machine["name"], "storage": machine.get("storage") or {}}]
        for target in targets:
            distro = (target.get("storage") or {}).get("wsl_distro")
            if not distro:
                continue
            base = target.get("runner_base_dir")
            roots = (str(base),) if base else _default_roots(runner_user)
            hosts.append(Host(str(target["name"]), ssh, str(distro), runner_user, roots, "runner"))
    return hosts


def hosts_from_workstations(entries: Iterable[dict[str, Any]]) -> list[Host]:
    hosts = []
    for entry in entries:
        user = str(entry["runner_user"])
        hosts.append(Host(str(entry["name"]), str(entry["ssh"]), None, user, _default_roots(user), "workstation"))
    return hosts


def load_workstations(path: Path) -> list[Host]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return hosts_from_workstations(data["workstations"])


def load_inventory(registry_path: Path | None, workstations_path: Path, runner_user: str) -> list[Host]:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from machine_registry import load_machine_registry  # noqa: PLC0415 - backend is not a package

    hosts = hosts_from_registry(load_machine_registry(registry_path), runner_user=runner_user)
    return hosts + load_workstations(workstations_path)


# --- payload & commands -------------------------------------------------------------


def build_payload(deploy_dir: Path = DEPLOY_DIR) -> str:
    """Base64 tar.gz of the installer + cleanup script, LF-normalised and executable."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in PAYLOAD_FILES:
            data = (deploy_dir / name).read_bytes().replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o755, 0
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def bootstrap_script(host: Host, *, verify_only: bool, dry_run: bool, source_rev: str = "unknown") -> str:
    """Remote shell: unpack the stdin payload, escalate if needed, run the installer.

    Exit 3 (needs-sudo) when a non-root login cannot sudo without a password —
    the operator runs the printed command by hand; we never handle passwords.
    """
    env = " ".join(
        f"{k}={shlex.quote(v)}"
        for k, v in (
            ("RUNNER_USER", host.runner_user),
            ("RUNNER_ROOTS", ":".join(host.runner_roots)),
            ("STORAGE_GC_SOURCE_REV", source_rev),
        )
    )
    if verify_only:
        action = '"$work/install-storage-gc.sh" --verify'
        escalate = 'SUDO=""; [ "$(id -u)" = 0 ] || ! sudo -n true 2>/dev/null || SUDO="sudo -n"'
    else:
        action = f'"$work/install-storage-gc.sh" --profile {host.profile}' + (" --dry-run" if dry_run else "")
        escalate = (
            'SUDO=""\n'
            'if [ "$(id -u)" != 0 ]; then\n'
            '  if sudo -n true 2>/dev/null; then SUDO="sudo -n"\n'
            '  else echo "NEEDS_SUDO: passwordless sudo unavailable for $(id -un)"; exit 3; fi\n'
            "fi"
        )
    return "\n".join(
        [
            "set -euo pipefail",
            'work="$(mktemp -d)"; trap \'rm -rf "$work"\' EXIT',
            'base64 -d | tar -xz -C "$work"',
            escalate,
            f'$SUDO env {env} RUNNER_CLEANUP_SOURCE="$work/runner-cleanup.sh" bash {action}',
        ]
    )


def command_for(host: Host, *, local_name: str, script: str) -> list[str]:
    """argv that runs ``script`` as root in the host's distro (or as the login user on native Linux).

    The script travels base64-encoded in the argv (it is small); the payload
    travels on stdin, which keeps every hop under cmd.exe's 8K line limit.
    """
    b64 = base64.b64encode(script.encode()).decode("ascii")
    inner = f"source <(echo {b64} | base64 -d)"
    is_local = local_name.lower() in {host.ssh.lower(), host.name.lower()}
    if host.distro is not None and is_local:
        return ["wsl", "-d", host.distro, "-u", "root", "-e", "bash", "-c", inner]
    if host.distro is not None:
        return ["ssh", *SSH_OPTIONS, host.ssh, f'wsl -d {host.distro} -u root -e bash -c "{inner}"']
    return ["ssh", *SSH_OPTIONS, host.ssh, f'bash -c "{inner}"']


# --- reporting ---------------------------------------------------------------------


def parse_report(rc: int, stdout: str, host: str = "") -> HostReport:
    failed = tuple(re.findall(r"^check=(\S+) status=FAIL", stdout, flags=re.MULTILINE))
    verdicts = re.findall(r"^storage_gc_verify=(\S+)", stdout, flags=re.MULTILINE)
    verdict = verdicts[-1] if verdicts else None
    if rc in RC_STATUS:
        status = RC_STATUS[rc]
    elif rc == 0 and verdict == "ok":
        status = "ok"
    elif rc == 0 and verdict == "skipped":
        status = "dry-run"
    else:
        status = "drift"  # rc 1, any other rc, or exit 0 without a verdict
    return HostReport(host=host, status=status, failed=failed, output=stdout)


def _subprocess_runner(cmd: list[str], stdin: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # noqa: S603 - argv built from validated Host fields
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=HOST_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except FileNotFoundError as exc:
        return 255, str(exc)
    return proc.returncode, proc.stdout + proc.stderr


def run_fleet(
    hosts: Sequence[Host],
    *,
    runner: Runner = _subprocess_runner,
    payload: str,
    local_name: str,
    verify_only: bool = False,
    dry_run: bool = False,
    source_rev: str = "unknown",
) -> list[HostReport]:
    """Install (or verify) on every host concurrently; reports keep input order."""

    def one(host: Host) -> HostReport:
        script = bootstrap_script(host, verify_only=verify_only, dry_run=dry_run, source_rev=source_rev)
        rc, out = runner(command_for(host, local_name=local_name, script=script), payload)
        report = parse_report(rc, out, host=host.name)
        log.info("%s: %s%s", host.name, report.status, f" failed={','.join(report.failed)}" if report.failed else "")
        return report

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        return list(pool.map(one, hosts))


def manual_install_hint(host: Host) -> str:
    """One command (run from the repo root) for a host whose sudo needs a password.

    The operator types the password into their own terminal; this tool never sees it.
    """
    env = f"RUNNER_USER={host.runner_user} RUNNER_ROOTS={':'.join(host.runner_roots)}"
    return (
        f"scp deploy/install-storage-gc.sh deploy/runner-cleanup.sh {host.ssh}:/tmp/"
        f" && ssh -t {host.ssh} 'sudo {env} RUNNER_CLEANUP_SOURCE=/tmp/runner-cleanup.sh"
        f" bash /tmp/install-storage-gc.sh --profile {host.profile}'"
    )


def exit_code(results: Sequence[HostReport]) -> int:
    if not results:
        return 2
    return 0 if all(r.status in SUCCESS_STATUSES for r in results) else 1


def _source_rev() -> str:
    proc = subprocess.run(  # noqa: S603, S607 - fixed argv
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hosts", help="comma-separated host names (default: all)")
    parser.add_argument("--list", action="store_true", help="print the inventory and exit")
    parser.add_argument("--verify-only", action="store_true", help="check every host; install nothing")
    parser.add_argument("--dry-run", action="store_true", help="show what the installer would change")
    parser.add_argument("--runner-user", default=DEFAULT_RUNNER_USER)
    parser.add_argument("--registry", type=Path, default=None, help="machine registry (default: backend's)")
    parser.add_argument("--workstations", type=Path, default=DEFAULT_WORKSTATIONS)
    parser.add_argument("--show-output", action="store_true", help="print each host's full installer output")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    hosts = load_inventory(args.registry, args.workstations, args.runner_user)
    if args.hosts:
        wanted = {h.strip().lower() for h in args.hosts.split(",") if h.strip()}
        unknown = wanted - {h.name.lower() for h in hosts}
        if unknown:
            sys.stderr.write(f"unknown host(s): {', '.join(sorted(unknown))}\n")
            return 2
        hosts = [h for h in hosts if h.name.lower() in wanted]

    if args.list:
        for h in hosts:
            sys.stdout.write(f"{h.name:22} ssh={h.ssh:14} distro={h.distro or '-':20} profile={h.profile}\n")
        return 0 if hosts else 2

    results = run_fleet(
        hosts,
        payload=build_payload(),
        local_name=platform.node(),
        verify_only=args.verify_only,
        dry_run=args.dry_run,
        source_rev=_source_rev(),
    )
    by_name = {h.name: h for h in hosts}
    for r in results:
        if args.show_output or r.status not in SUCCESS_STATUSES:
            sys.stdout.write(f"===== {r.host}: {r.status}\n{r.output.rstrip()}\n")
        if r.status == "needs-sudo":
            sys.stdout.write(f"  run this yourself (prompts for {r.host}'s sudo password):\n")
            sys.stdout.write(f"  {manual_install_hint(by_name[r.host])}\n")
    sys.stdout.write("\n".join(f"{r.host:22} {r.status:12} {','.join(r.failed)}" for r in results) + "\n")
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
