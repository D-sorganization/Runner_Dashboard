"""Release qualification preflight (#3596)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import brick_release_qualify as brq  # noqa: E402


def test_codex_chat_argv_check_reports_fix3595_until_merged() -> None:
    result = brq.check_codex_chat_argv()
    if result.ok:
        assert "contract ok" in result.detail
    else:
        assert "#3595" in result.detail


def test_run_qualify_marks_deferred_worker_step() -> None:
    report = brq.run_qualify("http://127.0.0.1:9")  # connection refused is ok for this assertion
    deferred = [c for c in report.checks if c.name == "live_codex_worker_task"]
    assert len(deferred) == 1 and deferred[0].deferred is True


def test_report_ready_false_when_hub_down(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(brq, "check_hub_health", lambda *a, **k: brq.CheckResult("hub_health", False, "down"))
    monkeypatch.setattr(brq, "check_fleet_status", lambda *a, **k: brq.CheckResult("fleet_status", True))
    monkeypatch.setattr(brq, "check_coordination_jobs", lambda *a, **k: brq.CheckResult("coordination_jobs", True))
    monkeypatch.setattr(brq, "check_codex_chat_argv", lambda: brq.CheckResult("codex_chat_argv", True))
    report = brq.run_qualify("http://example.invalid")
    assert report.to_dict()["ready_for_upgrade"] is False
