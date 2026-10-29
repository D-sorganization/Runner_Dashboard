"""deploy/security-scan-host/setup-build-network.sh: dry run by default, refuses CT (#2572)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from bash_host import BASH

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "security-scan-host" / "setup-build-network.sh"
pytestmark = pytest.mark.skipif(sys.platform == "win32" or BASH is None, reason="needs POSIX bash")


def _run(tmp_path: Path, hostname: str, *args: str) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for name, body in (("hostname", f"echo {hostname}"), ("docker", "exit 1"), ("iptables", "exit 1")):
        tool = stub / name
        tool.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        tool.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}
    return subprocess.run([str(BASH), str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False)


def test_dry_run_prints_the_firewall_and_builder(tmp_path: Path) -> None:
    result = _run(tmp_path, "scanvm")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "DRY RUN: docker network create --driver bridge --ipv6=false" in out
    assert "DOCKER-USER 1 -i br-vsbuild -j VS_BUILD" in out
    for cidr in ("10.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "192.168.0.0/16"):
        assert f"-d {cidr} -j DROP" in out
    assert "INPUT 1 -i br-vsbuild -j DROP" in out
    assert "--name vs-build-builder --driver docker-container --driver-opt network=vs-build" in out


def test_refuses_disallowed_host_and_bad_names(tmp_path: Path) -> None:
    # deploy/security-scan-host/setup-build-network.sh stays untouched under #2657.
    assert _run(tmp_path, "ControlTower").returncode == 3  # pragma: allowlist private-topology
    assert _run(tmp_path, "scanvm", "--network", "x;rm").returncode == 2
    assert _run(tmp_path, "scanvm", "--dns", "1.1.1.1 evil]").returncode == 2


def test_builder_pins_dns_and_the_marker_is_written_last(tmp_path: Path) -> None:
    out = _run(tmp_path, "scanvm").stdout
    assert '[dns] nameservers = ["1.1.1.1","9.9.9.9"]' in out
    assert "--config /run/vs-build/vs-build.buildkitd.toml" in out
    lines = out.splitlines()
    assert "rm -f /run/vs-build/vs-build.ok" in out
    assert lines[-2] == "DRY RUN: write /run/vs-build/vs-build.ok (network id)"
