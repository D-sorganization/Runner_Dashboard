"""Artifact wheelhouse ABI contract and fail-closed installation (#1212).

v4.10.0 was installed on a host whose only Python 3.12 had no ``pip``. The
installer rejected every interpreter (it demanded host ``python -m pip``) but
only after ``rsync --delete`` had already replaced the deploy dir and deleted
``.venv``, leaving the service unable to start. These tests pin the fix:

* the wheelhouse ABI check rejects wheels the declared ``python_minor`` cannot
  install (and accepts ``abi3``/pure wheels that it can);
* the installer fails *before* touching the deploy dir when the wheelhouse,
  the interpreter, or the offline dependency preflight is wrong;
* an interpreter with ``venv``+``ensurepip`` but no host ``pip`` qualifies for
  installation (the venv carries its own pip).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
INSTALLER = DEPLOY / "install-dashboard-artifact.sh"
PACKAGER = DEPLOY / "package-dashboard-artifact.sh"
RUNTIME_LIB = DEPLOY / "python-runtime.sh"
ABI_CHECK = DEPLOY / "check-wheelhouse-abi.py"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _load_abi_check():
    spec = importlib.util.spec_from_file_location("check_wheelhouse_abi", ABI_CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


abi = _load_abi_check()

CFFI_311 = "cffi-2.0.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
CFFI_312 = "cffi-2.0.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
CRYPTO_ABI3 = "cryptography-50.0.0-cp311-abi3-manylinux_2_34_x86_64.whl"
PSUTIL_ABI3 = "psutil-7.2.2-cp36-abi3-manylinux2010_x86_64.manylinux_2_12_x86_64.manylinux_2_28_x86_64.whl"
PURE = "httpx-0.28.1-py3-none-any.whl"


# ─── Wheelhouse ABI check ───────────────────────────────────────────────────


@pytest.mark.parametrize("wheel", [CFFI_312, CRYPTO_ABI3, PSUTIL_ABI3, PURE, "six-1.17.0-py2.py3-none-any.whl"])
def test_abi_check_accepts_wheels_installable_on_declared_minor(wheel: str) -> None:
    assert abi.wheel_problem(wheel, 12) is None


@pytest.mark.parametrize(
    ("wheel", "needle"),
    [
        (CFFI_311, "ABI cp311-cp311"),
        ("cffi-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl", "ABI cp313-cp313"),
        ("pkg-1.0-cp313-abi3-manylinux_2_17_x86_64.whl", "ABI cp313-abi3"),
        ("pkg-1.0-cp313-cp313t-manylinux_2_17_x86_64.whl", "ABI cp313-cp313t"),
        ("cffi-2.0.0-cp312-cp312-win_amd64.whl", "platform win_amd64"),
        ("cffi-2.0.0-cp312-cp312-macosx_11_0_arm64.whl", "platform macosx"),
        ("not-a-wheel.whl", "malformed"),
    ],
)
def test_abi_check_rejects_wheels_the_declared_minor_cannot_install(wheel: str, needle: str) -> None:
    problem = abi.wheel_problem(wheel, 12)
    assert problem is not None and needle in problem


def test_abi_check_cli_reports_every_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name in (CFFI_311, CFFI_312, PURE, "pydantic_core-2.46.3-cp311-cp311-manylinux_2_17_x86_64.whl"):
        (tmp_path / name).write_bytes(b"")
    assert abi.main(["--python-minor", "3.12", "--wheel-dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "2 wheel(s) incompatible" in err
    assert "cffi-2.0.0-cp311" in err and "pydantic_core-2.46.3-cp311" in err

    for name in (CFFI_311, "pydantic_core-2.46.3-cp311-cp311-manylinux_2_17_x86_64.whl"):
        (tmp_path / name).unlink()
    assert abi.main(["--python-minor", "3.12", "--wheel-dir", str(tmp_path)]) == 0


@pytest.mark.parametrize("value", ["3", "3.x", "2.7", "312", ""])
def test_abi_check_rejects_malformed_python_minor(tmp_path: Path, value: str) -> None:
    assert abi.main(["--python-minor", value, "--wheel-dir", str(tmp_path)]) == 2


def test_packaging_pins_and_checks_declared_minor() -> None:
    package = PACKAGER.read_text(encoding="utf-8")
    assert "--python-minor) TARGET_PYTHON_MINOR=" in package
    assert 'select_dashboard_python "${TARGET_PYTHON_MINOR}" pip' in package
    assert "check-wheelhouse-abi.py" in package
    # The ABI check runs on the staged wheelhouse before deployment.json/tarball are produced.
    assert package.index("check-wheelhouse-abi.py") < package.index("Generate deployment.json")

    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert 'ARTIFACT_PYTHON_MINOR: "3.12"' in workflow
    assert '--python-minor "$ARTIFACT_PYTHON_MINOR"' in workflow


def test_installer_preflights_before_first_deploy_dir_mutation() -> None:
    src = INSTALLER.read_text(encoding="utf-8")
    first_mutation = src.index('mkdir -p "${DEPLOY_DIR}"')
    gates = ("check-wheelhouse-abi.py", 'select_dashboard_python "${ARTIFACT_PYTHON_MINOR}" venv', "preflight-venv")
    for gate in gates:
        assert src.index(gate) < first_mutation, gate
    assert src.index('build_runtime_venv "${RUNTIME_PYTHON}" "${live_venv}"') < src.index("rsync -a --delete")
    assert "--exclude='/.venv'" in src
    assert "/.venv/bin/pip" not in src, "installs must use the venv's own `python -m pip`"


# ─── Behavioural: drive the real scripts through bash ───────────────────────


def _bash(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, "-c", script, "_", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env={**os.environ, **(env or {})},
    )


def _host_tools_ready() -> bool:
    if BASH is None or os.name == "nt":
        return False
    probe = _bash("command -v python3 && command -v rsync && command -v sha256sum && command -v tar")
    return probe.returncode == 0


needs_host = pytest.mark.skipif(not _host_tools_ready(), reason=f"{SKIP_REASON}; plus Linux python3/rsync/sha256sum")


def _host_minor() -> str | None:
    out = _bash(f'source "{as_bash_path(RUNTIME_LIB)}" && dashboard_python_minor python3')
    return out.stdout.strip() if out.returncode == 0 else None


def _make_artifact(root: Path, python_minor: str, wheels: tuple[str, ...] = ()) -> Path:
    stage = root / "stage"
    for rel, body in {
        "VERSION": "9.9.9\n",
        "local_apps.json": "[]\n",
        "refresh-token.sh": "#!/bin/bash\nexit 0\n",
        "wsl-mirrored-port-helper.sh": "#!/bin/bash\nexit 0\n",
        "requirements.lock.txt": "",
        "backend/server.py": "# new server\n",
        "frontend/index.html": "<!doctype html>\n",
        "deploy/setup.sh": "#!/bin/bash\n",
    }.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_text(body, encoding="utf-8")
    (stage / "backend" / "wheels").mkdir()
    for wheel in wheels:
        (stage / "backend" / "wheels" / wheel).write_bytes(b"")
    compat = {
        "artifact_schema": "runner-dashboard-artifact-v2",
        "python_requires": ">=3.11,<3.14",
        "python_minor": python_minor,
        "service_name": "runner-dashboard.service",
    }
    meta = {"version": "9.9.9", "git_sha": "0" * 40, "build_timestamp": "2026-09-22T00:00:00Z", "compatibility": compat}
    (stage / "deployment.json").write_text(json.dumps(meta), encoding="utf-8")
    files = sorted([p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()] + ["FILES.txt"])
    (stage / "FILES.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    tarball = root / "dashboard-9.9.9.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for item in stage.iterdir():
            tar.add(item, arcname=item.name)
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    Path(f"{tarball}.sha256").write_text(f"{digest}  {tarball.name}\n", encoding="utf-8")
    return tarball


def _live_install(root: Path) -> Path:
    deploy = root / "live"
    (deploy / ".venv" / "bin").mkdir(parents=True)
    (deploy / ".venv" / "sentinel").write_text("old venv\n", encoding="utf-8")
    (deploy / "backend").mkdir()
    (deploy / "backend" / "server.py").write_text("# old server\n", encoding="utf-8")
    (deploy / "backend" / "only_in_old.py").write_text("# rsync --delete would remove me\n", encoding="utf-8")
    return deploy


def _assert_untouched(deploy: Path) -> None:
    assert (deploy / ".venv" / "sentinel").read_text(encoding="utf-8") == "old venv\n"
    assert (deploy / "backend" / "server.py").read_text(encoding="utf-8") == "# old server\n"
    assert (deploy / "backend" / "only_in_old.py").exists()
    assert not (deploy / "deployment.json").exists()
    assert not (deploy / ".venv.previous-install").exists()


def _install(tarball: Path, deploy: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return _bash(
        'bash "$1" --artifact "$2" --deploy-dir "$3"',
        as_bash_path(INSTALLER),
        as_bash_path(tarball),
        as_bash_path(deploy),
        env=env,
    )


@needs_host
def test_installer_rejects_cp311_wheelhouse_in_312_artifact_without_touching_deploy(tmp_path: Path) -> None:
    tarball = _make_artifact(tmp_path, "3.12", (CFFI_311, PURE))
    deploy = _live_install(tmp_path)
    result = _install(tarball, deploy)
    assert result.returncode != 0
    assert "cffi-2.0.0-cp311" in result.stderr
    _assert_untouched(deploy)


@needs_host
def test_installer_fails_closed_when_no_interpreter_matches(tmp_path: Path) -> None:
    tarball = _make_artifact(tmp_path, "3.99", (PURE,))
    deploy = _live_install(tmp_path)
    result = _install(tarball, deploy)
    assert result.returncode != 0
    assert "No supported Python found" in result.stderr
    _assert_untouched(deploy)


@needs_host
def test_installer_fails_closed_when_offline_preflight_fails(tmp_path: Path) -> None:
    minor = _host_minor()
    if minor is None:
        pytest.skip("host python3 is outside the supported 3.11-3.13 range")
    tarball = _make_artifact(tmp_path, minor)  # empty wheelhouse: `import fastapi` cannot pass
    deploy = _live_install(tmp_path)
    result = _install(tarball, deploy, env={"RUNNER_DASHBOARD_PYTHON": "python3"})
    assert result.returncode != 0
    assert "deploy dir untouched" in result.stdout + result.stderr
    _assert_untouched(deploy)


@needs_host
def test_venv_capability_accepts_interpreter_without_host_pip(tmp_path: Path) -> None:
    venv_probe = _bash('d=$(mktemp -d) && python3 -m venv "$d/v"; rc=$?; rm -rf "$d"; exit $rc')
    if _host_minor() is None or venv_probe.returncode:
        pytest.skip("host python3 cannot bootstrap a venv")
    shim = tmp_path / "python-no-pip"
    real = _bash("command -v python3").stdout.strip()
    shim.write_text(
        f'#!/usr/bin/env bash\n[[ "$1" == "-m" && "$2" == "pip" ]] && exit 1\nexec "{real}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    select = f'source "{as_bash_path(RUNTIME_LIB)}" && select_dashboard_python "" "$1"'
    env = {"RUNNER_DASHBOARD_PYTHON": as_bash_path(shim)}

    via_venv = _bash(select, "venv", env=env)
    assert via_venv.returncode == 0, via_venv.stderr
    assert via_venv.stdout.strip() == as_bash_path(shim)

    via_pip = _bash(select, "pip", env=env)
    assert via_pip.stdout.strip() != as_bash_path(shim), "pip capability must still demand host pip"


def test_selector_rejects_unknown_capability() -> None:
    if BASH is None:
        pytest.skip(SKIP_REASON)
    result = _bash(f'source "{as_bash_path(RUNTIME_LIB)}" && select_dashboard_python "" bogus')
    assert result.returncode == 2
    assert "unknown capability" in result.stderr
