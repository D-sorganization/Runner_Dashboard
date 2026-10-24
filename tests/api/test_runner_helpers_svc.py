"""Router runner_helpers service control contract (#2531)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import dashboard_config as dc  # noqa: E402
from routers import runner_helpers  # noqa: E402


@pytest.mark.asyncio
async def test_run_runner_svc_runs_from_runner_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dc, "RUNNER_BASE_DIR", tmp_path)
    monkeypatch.setattr(runner_helpers, "RUNNER_BASE_DIR", tmp_path)
    runner_dir = tmp_path / "runner-2"
    runner_dir.mkdir(parents=True)
    (runner_dir / "svc.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    captured: dict = {}

    async def fake_run_cmd(cmd, timeout=30, cwd=None):  # noqa: ANN001, ARG001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return 0, "ok", ""

    monkeypatch.setattr(runner_helpers, "run_cmd", fake_run_cmd)
    code, stdout, _ = await runner_helpers.run_runner_svc(2, "start")
    assert code == 0 and stdout == "ok"
    assert captured["cwd"] == runner_dir
    assert str(runner_dir / "svc.sh") in captured["cmd"][-2]
