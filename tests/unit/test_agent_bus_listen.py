"""#3682 S2: lane-side agent-bus listener helpers."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from bash_host import BASH, SKIP_REASON  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("agent_bus_listen", _ROOT / "scripts" / "agent_bus_listen.py")
assert _spec and _spec.loader
listen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(listen)


@pytest.mark.unit
def test_parse_sse_skips_comments_and_keeps_ids() -> None:
    lines = [
        "retry: 2000\n",
        "\n",
        ": keep-alive\n",
        "\n",
        "id: 7\n",
        "event: message\n",
        'data: {"id": "abm_1"}\n',
        "\n",
    ]
    assert list(listen.parse_sse(lines)) == [("message", "7", '{"id": "abm_1"}')]


@pytest.mark.unit
def test_wake_writes_file_atomically(tmp_path: Path) -> None:
    wake = tmp_path / "lane.wake"
    listen._wake(wake, None, {"id": "abm_2", "from_session": "barb-x"})
    assert json.loads(wake.read_text())["id"] == "abm_2"


@pytest.mark.unit
@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_wait_script_returns_early_on_wake(tmp_path: Path) -> None:
    wake = tmp_path / "lane.wake"
    wake.write_text("0")
    threading.Timer(1.2, lambda: listen._wake(wake, None, {"id": "x"})).start()
    start = time.monotonic()
    rc = subprocess.run(
        [str(BASH), str(_ROOT / "scripts" / "agent_bus_wait.sh"), str(wake), "20"], check=False
    ).returncode
    assert rc == 0 and time.monotonic() - start < 5
