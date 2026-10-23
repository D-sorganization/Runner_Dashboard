"""The scanned source: tarball download, docker env, contract recheck (#2572, #2603).

The default-branch SHA arrives as a tarball (no ``.git``, no credentials), is
extracted under size caps, and its own ``.oss-scanner/`` contract decides
whether and how it is built (panel q5).
"""

from __future__ import annotations

import io
import os
import tarfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO

from vuln_scan.contract import PROJECT_PATH, check_contract
from vuln_scan.operator_config import ScanHost
from vuln_scan.watch import ScanAborted

MAX_SOURCE_BYTES = 2 * 1024**3
MAX_SOURCE_FILES = 200_000
_DOCKER_ENV_KEYS = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT")


def docker_env(host: ScanHost, base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k.upper() in _DOCKER_ENV_KEYS}
    if host.docker_host:
        env["DOCKER_HOST"] = host.docker_host
    env["DOCKER_BUILDKIT"] = "1"
    return env


def extract_tarball(stream: IO[bytes], dest: Path) -> int:
    """Extract a GitHub tarball (one top-level dir) into ``dest``; return file count."""
    count = 0
    total = 0
    with tarfile.open(fileobj=stream, mode="r|gz") as tar:
        for member in tar:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            member.name = parts[1]
            if member.issym() or member.islnk() or member.isdev():
                continue
            count += 1
            total += member.size
            if count > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
                raise ScanAborted("source tarball exceeds the size cap")
            tar.extract(member, dest, filter="data")
    return count


def codeload_fetch(org: str) -> Callable[[str, str, Path], None]:
    """Fetch a public repository's tarball at ``sha``; no token is ever sent."""
    import urllib.request

    def fetch(repo: str, sha: str, dest: Path) -> None:
        url = f"https://codeload.github.com/{org}/{repo}/tar.gz/{sha}"
        with urllib.request.urlopen(url, timeout=300) as resp:  # noqa: S310 - fixed https host
            extract_tarball(io.BufferedReader(resp), dest)

    return fetch


def _file_size(root: Path, rel: str) -> int | None:
    """Size of ``rel`` under ``root``, or None if absent or outside ``root``."""
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return path.stat().st_size


def local_contract(org: str, repo: str, context: Path) -> str:
    """Validate ``.oss-scanner/`` in the downloaded source; return its Dockerfile.

    Admission saw the contract through the API, which may be a different SHA;
    the build must follow the contract that is actually being built (q5).
    """
    project = context / PROJECT_PATH
    text = (
        project.read_text(encoding="utf-8", errors="replace")
        if project.is_file()
        else None
    )
    first = check_contract(org=org, repo=repo, project_yaml=text, files={})
    files = {
        path: _file_size(context, path)
        for path in (first.dockerfile, first.threat_model)
        if path
    }
    report = check_contract(org=org, repo=repo, project_yaml=text, files=files)
    if not report.valid or not report.dockerfile:
        problems = "; ".join(report.problems) or "no project.yaml"
        raise ScanAborted(f"contract at the scanned SHA is invalid: {problems}")
    if report.disabled:
        raise ScanAborted("contract at the scanned SHA sets disabled: true")
    return report.dockerfile
