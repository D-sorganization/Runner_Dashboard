"""Shared pid probe, kill-0 gate and lane launcher (#4421)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import check_no_kill_zero as gate  # noqa: E402
import pid_probe  # noqa: E402

LAUNCHER = _ROOT / "scripts" / "lanes" / "lane-launch.ps1"


# --- pid_alive: contract ------------------------------------------------------


@pytest.mark.parametrize("pid", [0, -1, -4321, True, None, "12", 1.5])
def test_non_positive_or_non_int_pid_is_never_alive(pid: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any) -> None:
        raise AssertionError("no probe may run for an invalid pid")

    monkeypatch.setattr(pid_probe, "_pid_alive_windows", boom)
    monkeypatch.setattr(pid_probe, "_pid_alive_posix", boom)
    assert pid_probe.pid_alive(pid) is False


def test_own_process_is_alive() -> None:
    """Safe on Windows too: the probe sends nothing to the console."""
    assert pid_probe.pid_alive(os.getpid()) is True


def test_exited_child_is_not_alive() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=60)
    # On Windows the Popen object still holds a handle, so the pid stays queryable but reads as exited.
    assert pid_probe.pid_alive(child.pid) is False


# --- pid_alive: Windows path never signals ------------------------------------


def test_windows_dispatch_never_calls_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression: on nt, os.kill(pid, 0) is CTRL_C_EVENT for the whole console."""

    def forbidden_kill(pid: int, sig: int) -> None:
        raise AssertionError(f"os.kill({pid}, {sig}) called on the Windows path")

    monkeypatch.setattr(pid_probe, "os", SimpleNamespace(name="nt", kill=forbidden_kill))
    seen: list[int] = []
    monkeypatch.setattr(pid_probe, "_pid_alive_windows", lambda pid: seen.append(pid) or True)
    assert pid_probe.pid_alive(4321) is True
    assert seen == [4321]


class _FakeKernel32:
    def __init__(self, *, handle: int, exit_code: int | None) -> None:
        self.handle, self.exit_code = handle, exit_code
        self.opened: list[tuple[int, bool, int]] = []
        self.closed: list[int] = []
        # ctypes-style functions carry restype/argtypes attributes and are callable.
        self.OpenProcess = _Callable(self._open)
        self.GetExitCodeProcess = _Callable(self._exit_code)
        self.CloseHandle = _Callable(self._close)

    def _open(self, access: int, inherit: bool, pid: int) -> int:
        self.opened.append((access, inherit, pid))
        return self.handle

    def _exit_code(self, handle: int, out: Any) -> int:
        if self.exit_code is None:
            return 0
        out._obj.value = self.exit_code
        return 1

    def _close(self, handle: int) -> int:
        self.closed.append(handle)
        return 1


class _Callable:
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def __call__(self, *args: Any) -> Any:
        return self._fn(*args)


def _windows_probe(monkeypatch: pytest.MonkeyPatch, fake: _FakeKernel32, last_error: int = 0) -> bool:
    monkeypatch.setattr(pid_probe, "_kernel32", lambda: fake)
    monkeypatch.setattr(pid_probe, "_last_error", lambda: last_error)
    return pid_probe._pid_alive_windows(4321)


def test_windows_running_process_is_alive_and_handle_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(handle=77, exit_code=259)
    assert _windows_probe(monkeypatch, fake) is True
    assert fake.opened == [(0x1000, False, 4321)]  # PROCESS_QUERY_LIMITED_INFORMATION only
    assert fake.closed == [77]


def test_windows_exited_process_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(handle=77, exit_code=0)
    assert _windows_probe(monkeypatch, fake) is False
    assert fake.closed == [77]


def test_windows_exit_code_query_failure_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(handle=77, exit_code=None)
    assert _windows_probe(monkeypatch, fake) is False
    assert fake.closed == [77]


def test_windows_no_such_process_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(handle=0, exit_code=259)
    assert _windows_probe(monkeypatch, fake, last_error=87) is False  # ERROR_INVALID_PARAMETER
    assert fake.closed == []


def test_windows_access_denied_means_the_process_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(handle=0, exit_code=259)
    assert _windows_probe(monkeypatch, fake, last_error=5) is True


# --- pid_alive: POSIX path ------------------------------------------------------


def _posix_probe(monkeypatch: pytest.MonkeyPatch, error: BaseException | None) -> tuple[bool, list[tuple[int, int]]]:
    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if error is not None:
            raise error

    monkeypatch.setattr(pid_probe, "os", SimpleNamespace(name="posix", kill=fake_kill))
    return pid_probe.pid_alive(4321), calls


def test_posix_uses_signal_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _posix_probe(monkeypatch, None) == (True, [(4321, 0)])


def test_posix_missing_process_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _posix_probe(monkeypatch, ProcessLookupError())[0] is False


def test_posix_permission_error_means_the_process_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _posix_probe(monkeypatch, PermissionError())[0] is True


def test_posix_other_os_error_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _posix_probe(monkeypatch, OSError("boom"))[0] is False


# --- gate ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.kill(pid, 0)\n",
        "import os\nos.kill(os.getpid(), 0)\n",
        "import os as _os\n_os.kill(pid, 0)\n",
        "from os import kill\nkill(pid, 0)\n",
        "from os import kill as k\nk(pid, 0)\n",
        "import os, signal\nos.kill(pid, signal.CTRL_C_EVENT)\n",
        "import os\nfrom signal import CTRL_C_EVENT\nos.kill(pid, CTRL_C_EVENT)\n",
        "import os\nos.kill(pid, signal=0)\n",
        "import os\ndef f(pid):\n    try:\n        os.kill(pid, 0)\n    except OSError:\n        return False\n",
    ],
)
def test_gate_flags_seeded_violation(source: str) -> None:
    found = gate.find_violations(source, "seeded.py")
    assert len(found) == 1 and found[0].startswith("seeded.py:")
    assert "pid_probe" in found[0]


@pytest.mark.parametrize(
    "source",
    [
        "import os, signal\nos.kill(pid, signal.SIGTERM)\n",
        "import os\nos.kill(pid, 9)\n",
        "import os\nos.kill(pid, sig)\n",
        "import os, signal\nos.killpg(os.getpgid(pid), signal.SIGTERM)\n",
        "thing.kill(pid, 0)\n",
        "def kill(pid, sig): ...\nkill(pid, 0)\n",
        "from pid_probe import pid_alive\npid_alive(pid)\n",
        "import os\nos.kill(pid, False)\n",
    ],
)
def test_gate_allows_other_calls(source: str) -> None:
    assert gate.find_violations(source, "ok.py") == []


def test_gate_cli_is_red_on_a_seeded_file_and_green_without_it(tmp_path: Path) -> None:
    bad = tmp_path / "seeded.py"
    bad.write_text("import os\nos.kill(123, 0)\n", encoding="utf-8")
    good = tmp_path / "fine.py"
    good.write_text("import os, signal\nos.kill(123, signal.SIGTERM)\n", encoding="utf-8")
    script = str(_ROOT / "scripts" / "check_no_kill_zero.py")
    red = subprocess.run([sys.executable, script, str(bad), str(good)], capture_output=True, text=True, timeout=60)
    assert red.returncode == 1
    assert "seeded.py:2" in red.stderr and "fine.py" not in red.stderr
    green = subprocess.run([sys.executable, script, str(good)], capture_output=True, text=True, timeout=60)
    assert green.returncode == 0, green.stderr


def test_gate_reports_an_unparsable_file_without_hiding_violations(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n", encoding="utf-8")
    bad = tmp_path / "seeded.py"
    bad.write_text("import os\nos.kill(123, 0)\n", encoding="utf-8")
    violations, unparsable = gate.check([broken, bad], root=tmp_path)
    assert len(violations) == 1 and violations[0].startswith("seeded.py:2")
    assert len(unparsable) == 1 and unparsable[0].startswith("broken.py")


def test_only_the_probe_itself_is_allow_listed() -> None:
    assert gate.ALLOWED == frozenset({"scripts/pid_probe.py"})
    source = (_ROOT / "scripts" / "pid_probe.py").read_text(encoding="utf-8")
    assert len(gate.find_violations(source, "scripts/pid_probe.py")) == 1  # the POSIX branch


def test_repository_has_no_kill_zero_outside_the_probe() -> None:
    """The CI gate: runs with the unit tests on every PR."""
    violations, _unparsable = gate.check(gate._repo_files(_ROOT), root=_ROOT)
    assert violations == []


def test_gate_is_a_pre_commit_hook() -> None:
    config = (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "id: no-kill-zero" in config
    assert "entry: python scripts/check_no_kill_zero.py" in config


# --- lane launcher ------------------------------------------------------------


def test_launcher_sets_ctrl_c_ignore_before_starting_the_agent() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "SetConsoleCtrlHandler([IntPtr]::Zero, $true)" in text
    assert text.index("SetConsoleCtrlHandler([IntPtr]::Zero, $true)") < text.index("& $Exe @a")
    assert "agent exited LASTEXITCODE" in text
    assert "xterm-256color" in text
    # Checked-in copy carries no host-specific log location.
    assert "overnight-" not in text


_PROBE = textwrap.dedent(
    """
    import os, signal, sys, time
    os.kill(os.getpid(), signal.CTRL_C_EVENT)
    time.sleep(2)
    open(sys.argv[1], "w").write("survived")
    """
)


@pytest.mark.skipif(sys.platform != "win32", reason="console CTRL_C blast exists only on Windows")
def test_launcher_self_test_agent_and_wrapper_survive_console_ctrl_c(tmp_path: Path) -> None:
    """A child sends CTRL_C_EVENT to its own console; under the launcher nothing dies.

    The launcher runs in its own hidden console (CREATE_NO_WINDOW), so the
    event cannot reach the console this test session is attached to.
    """
    import shutil

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not installed")
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    marker = tmp_path / "marker.txt"
    wrapper = tmp_path / "run-agent.cmd"
    wrapper.write_text(f'@"{sys.executable}" "{probe}" "{marker}"\r\n', encoding="utf-8")
    log_dir = tmp_path / "log"
    proc = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(LAUNCHER),
            "-Lane",
            "selftest",
            "-Exe",
            str(wrapper),
            "-WorkDir",
            str(tmp_path),
            "-LogDir",
            str(log_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined,unused-ignore]
    )
    log = (log_dir / "relaunch.log").read_text(encoding="utf-8-sig")
    assert marker.is_file() and marker.read_text(encoding="utf-8") == "survived", proc.stdout + proc.stderr + log
    assert "ctrlc_ignore=True" in log
    assert "agent exited LASTEXITCODE=0" in log
    assert "wrapper end" in log
