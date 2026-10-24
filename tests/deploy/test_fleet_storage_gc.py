"""Tests for deploy/fleet_storage_gc.py — the fleet-wide storage-GC rollout (#1925).

Pure functions are tested directly; the transport is injected, so no test ever
opens an SSH connection.
"""

from __future__ import annotations

import base64
import io
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))
sys.path.insert(0, str(REPO / "backend"))

import fleet_storage_gc as fsg  # noqa: E402
from machine_registry import load_machine_registry  # noqa: E402

RUNNER_USER = "fleet-operator"


def _host(**overrides: object) -> fsg.Host:
    fields: dict[str, object] = {
        "name": "node-a-Runner",
        "ssh": "node-a",
        "distro": "node-a-Runner",
        "runner_user": RUNNER_USER,
        "runner_roots": ("/home/fleet-operator/actions-runners",),
        "profile": "runner",
    }
    fields.update(overrides)
    return fsg.Host(**fields)  # type: ignore[arg-type]


# --- Host contract --------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"ssh": ""}, "ssh"),
        ({"profile": "laptop"}, "profile"),
        ({"runner_roots": ("relative/dir",)}, "absolute"),
        ({"runner_user": ""}, "runner_user"),
        ({"runner_roots": ("/a:b",)}, "':'"),
    ],
)
def test_host_rejects_invalid_fields(overrides: dict[str, object], needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        _host(**overrides)


# --- inventory --------------------------------------------------------------------


def test_registry_hosts_cover_every_live_runner_pool() -> None:
    hosts = {h.name: h for h in fsg.hosts_from_registry(load_machine_registry(), runner_user=RUNNER_USER)}
    assert set(hosts) == {"node-a-Runner", "node-b", "hub-a"}

    ct = hosts["node-a-Runner"]
    assert (ct.ssh, ct.distro, ct.runner_roots) == (
        "node-a",
        "node-a-Runner",
        ("/home/fleet-operator/actions-runners",),
    )
    assert hosts["node-b"].distro == "Ubuntu"
    assert hosts["node-b"].runner_roots == ("/home/fleet-operator/actions-runners",)
    assert hosts["hub-a"].distro == "Ubuntu-22.04"
    assert all(h.profile == "runner" for h in hosts.values())


def test_registry_skips_retired_pools_and_machines_without_distro() -> None:
    registry = {
        "machines": [
            {
                "name": "Hub",
                "tailscale_nodes": [{"name": "hub-wsl", "preferred": False}, {"name": "hub", "preferred": True}],
                "storage": {},
                "runner_pools": [
                    {"name": "Old", "retired": True, "runner_base_dir": "/x", "storage": {"wsl_distro": None}},
                    {"name": "Live", "runner_base_dir": "/srv/runners", "storage": {"wsl_distro": "Live"}},
                ],
            },
            {"name": "NoDistro", "tailscale_nodes": [{"name": "nd"}], "storage": {}, "runner_pools": []},
        ]
    }
    hosts = fsg.hosts_from_registry(registry, runner_user="ci")
    assert [(h.name, h.ssh, h.distro, h.runner_roots) for h in hosts] == [("Live", "hub", "Live", ("/srv/runners",))]


def test_workstations_are_native_linux_with_workstation_profile() -> None:
    hosts = fsg.hosts_from_workstations([{"name": "node-c", "ssh": "node-c", "runner_user": "fleet-operator"}])
    assert hosts == [
        fsg.Host(
            name="node-c",
            ssh="node-c",
            distro=None,
            runner_user="fleet-operator",
            runner_roots=("/home/fleet-operator/actions-runners",),
            profile="workstation",
        )
    ]


def test_committed_workstation_list_loads() -> None:
    hosts = fsg.load_workstations(REPO / "config" / "storage-gc-workstations.json")
    assert {h.name for h in hosts} >= {"node-c", "raspberrypi"}
    assert all(h.profile == "workstation" and h.distro is None for h in hosts)


# --- payload & commands -------------------------------------------------------------


def test_payload_is_a_tarball_of_the_installer_and_cleanup_with_lf_and_exec_bits() -> None:
    raw = base64.b64decode(fsg.build_payload())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == {"install-storage-gc.sh", "runner-cleanup.sh"}
        for name, member in members.items():
            assert member.mode & 0o111, name
            data = tar.extractfile(member).read()  # type: ignore[union-attr]
            assert b"\r\n" not in data, name
            assert data == (REPO / "deploy" / name).read_bytes().replace(b"\r\n", b"\n")


def test_bootstrap_passes_host_contract_to_installer() -> None:
    script = fsg.bootstrap_script(_host(profile="workstation"), verify_only=False, dry_run=True)
    assert "RUNNER_USER=fleet-operator" in script
    assert "RUNNER_ROOTS=/home/fleet-operator/actions-runners" in script
    assert "--profile workstation" in script
    assert "--dry-run" in script
    assert "sudo -n" in script
    assert "exit 3" in script  # needs-sudo contract
    assert "base64 -d" in script and "tar -xz" in script


def test_bootstrap_verify_only_never_installs() -> None:
    script = fsg.bootstrap_script(_host(), verify_only=True, dry_run=False)
    assert "--verify" in script
    assert "--profile" not in script


def _decoded(command: list[str]) -> str:
    tail = command[-1]
    b64 = tail.split("echo ", 1)[1].split(" ", 1)[0]
    return base64.b64decode(b64).decode()


def test_remote_wsl_command_goes_through_ssh_and_wsl_as_root() -> None:
    cmd = fsg.command_for(_host(), local_name="node-b", script="echo hi")
    assert cmd[0] == "ssh" and "BatchMode=yes" in cmd and cmd[-2] == "node-a"
    assert cmd[-1].startswith('wsl -d node-a-Runner -u root -e bash -c "source <(echo ')
    assert _decoded(cmd) == "echo hi"


def test_local_wsl_host_skips_ssh() -> None:
    host = _host(name="node-b", ssh="node-b", distro="Ubuntu")
    cmd = fsg.command_for(host, local_name="NODE_B", script="echo hi")
    assert cmd[:7] == ["wsl", "-d", "Ubuntu", "-u", "root", "-e", "bash"]
    assert _decoded(cmd) == "echo hi"


def test_native_linux_command_has_no_wsl() -> None:
    host = _host(name="node-c", ssh="node-c", distro=None, profile="workstation")
    cmd = fsg.command_for(host, local_name="node-b", script="echo hi")
    assert cmd[-2] == "node-c"
    assert "wsl" not in cmd[-1]
    assert _decoded(cmd) == "echo hi"


# --- report parsing -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rc", "stdout", "status"),
    [
        (0, "check=binary_hash status=ok\nstorage_gc_verify=ok\n", "ok"),
        (1, "check=binary_hash status=FAIL detail=x\nstorage_gc_verify=fail\n", "drift"),
        (2, "", "precondition"),
        (3, "NEEDS_SUDO\n", "needs-sudo"),
        (255, "", "unreachable"),
        (0, "no verdict line", "drift"),  # exit 0 alone proves nothing
    ],
)
def test_parse_report_status(rc: int, stdout: str, status: str) -> None:
    assert fsg.parse_report(rc, stdout).status == status


def test_parse_report_collects_failed_checks() -> None:
    report = fsg.parse_report(
        1,
        "check=binary_hash status=FAIL detail=x\ncheck=stamp status=ok\nstorage_gc_verify=fail\n",
    )
    assert report.failed == ("binary_hash",)


# --- orchestration ---------------------------------------------------------------------


def test_run_fleet_sends_payload_on_stdin_and_reports_each_host() -> None:
    seen: list[tuple[list[str], str]] = []

    def runner(cmd: list[str], stdin: str) -> tuple[int, str]:
        seen.append((cmd, stdin))
        if cmd[-2] == "node-c":
            return 3, "NEEDS_SUDO\n"
        return 0, "storage_gc_verify=ok\n"

    hosts = [_host(), _host(name="node-c", ssh="node-c", distro=None, profile="workstation")]
    results = fsg.run_fleet(hosts, runner=runner, payload="PAYLOAD", local_name="node-b")
    assert [(r.host, r.status) for r in results] == [("node-a-Runner", "ok"), ("node-c", "needs-sudo")]
    assert all(stdin == "PAYLOAD" for _, stdin in seen)
    assert fsg.exit_code(results) == 1


def test_exit_code_zero_only_when_every_host_is_ok() -> None:
    ok = fsg.HostReport(host="a", status="ok", failed=(), output="")
    assert fsg.exit_code([ok, ok]) == 0
    assert fsg.exit_code([]) == 2  # an empty selection is a usage error, not success


def test_main_rejects_unknown_host_filter(capsys: pytest.CaptureFixture[str]) -> None:
    assert fsg.main(["--hosts", "nonexistent-host", "--list"]) == 2
    assert "nonexistent-host" in capsys.readouterr().err


def test_main_list_prints_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert fsg.main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in ("node-a-Runner", "node-b", "hub-a", "node-c", "raspberrypi"):
        assert name in out


def test_needs_sudo_hint_is_one_runnable_interactive_command() -> None:
    """node-c has password sudo: the rollout must hand the operator an exact
    command (scp the payload, then an interactive ssh -t sudo) — never a password."""
    host = _host(name="node-c", ssh="node-c", distro=None, profile="workstation")
    hint = fsg.manual_install_hint(host)
    assert hint.startswith("scp ")
    assert "deploy/install-storage-gc.sh" in hint and "deploy/runner-cleanup.sh" in hint
    assert "ssh -t node-c" in hint
    assert "sudo RUNNER_USER=fleet-operator" in hint
    assert "--profile workstation" in hint
