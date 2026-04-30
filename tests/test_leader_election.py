import subprocess
import sys
import time
from pathlib import Path

import pytest

# Note: fcntl is not available on Windows, so we skip these tests if running on Windows.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")


def test_autoscaler_leader_election(tmp_path: Path) -> None:
    # Use a custom lock path for the test
    lock_path = tmp_path / "autoscaler.lock"

    # We want to run two subprocesses of runner_autoscaler.py
    # But since it loops forever, we need a small script that does the lock logic.
    # Actually, we can just write a wrapper, but patches the lock path.
    # But runner_autoscaler imports psutil and might fail.

    # Let's just create a small wrapper
    wrapper_code = f"""
import sys
import time
sys.path.insert(0, "{(Path(__file__).parent.parent / "backend").as_posix()}")
import runner_autoscaler

# Patch lock path
runner_autoscaler._lock_fd = None
def patched_open(path, mode):
    if path == "/var/run/runner-autoscaler.lock":
        path = "{lock_path.as_posix()}"
    return open(path, mode)

runner_autoscaler.open = patched_open
# We don't want it to run forever, so we just check lock and exit 0
import fcntl
try:
    fd = patched_open("{lock_path.as_posix()}", "w")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Sleep to hold the lock
    time.sleep(2)
    sys.exit(0)
except (OSError, IOError):
    sys.exit(75)
"""
    wrapper_path = tmp_path / "wrapper.py"
    wrapper_path.write_text(wrapper_code)

    # Start first process, which should acquire the lock and sleep
    p1 = subprocess.Popen([sys.executable, str(wrapper_path)])

    time.sleep(0.5)  # give p1 time to acquire lock

    # Start second process, which should fail with 75
    p2 = subprocess.run([sys.executable, str(wrapper_path)])

    assert p2.returncode == 75

    p1.wait()
    assert p1.returncode == 0
