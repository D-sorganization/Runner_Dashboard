"""Synthetic regression tests for durable report-run recovery in Runner_Dashboard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _make_synthetic_source(root: Path) -> Path:
    src = root / "source"
    (src / "scripts").mkdir(parents=True)
    (src / "figures").mkdir(parents=True)
    for name in ("write_values.py", "compile_report.py", "build_docx.py"):
        (src / "scripts" / name).write_text("# synthetic\n", encoding="utf-8")
    (src / "report.tex").write_text("\\begin{document}\nSynthetic\n\\end{document}\n", encoding="utf-8")
    (src / "inentec-report.sty").write_text("% sty\n", encoding="utf-8")
    (src / "inentec_logo.png").write_bytes(b"PNG")
    return src


@pytest.mark.parametrize("failure", ["timeout", "start"])
def test_report_run_timeout_and_start_failure_are_terminal(tmp_path: Path, failure: str) -> None:
    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "dest"

    def bad_runner(cmd: list[str], timeout_s: float) -> report_run.CommandResult:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_s)
        raise FileNotFoundError("synthetic executable missing")

    res = report_run.run_report_job(src, dest, "refine", runner=bad_runner)
    assert res["state"] == "failed"
    assert res["approval_ready"] is False
    assert res["artifacts"] == {}
    assert res["error_type"] in ("TimeoutExpired", "FileNotFoundError")
    assert json.loads((dest / "job-status.json").read_text(encoding="utf-8")) == res


def test_interrupted_restart_gets_fresh_id_and_drops_stale_artifacts(
    tmp_path: Path,
) -> None:
    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "job-status.json").write_text(
        json.dumps({"state": "running", "attempt_id": "old-attempt", "approval_ready": False}),
        encoding="utf-8",
    )
    stale_build = dest / "build"
    stale_build.mkdir()
    (stale_build / "report.pdf").write_bytes(b"%PDF-stale")
    (stale_build / "report.docx").write_bytes(b"PK-stale")

    res = report_run.run_report_job(src, dest, "missing_input")
    assert res["recovered_prior_state"] == "interrupted"
    assert res["attempt_id"] != "old-attempt"
    assert res["state"] == "blocked"
    assert res["approval_ready"] is False
    assert res["artifacts"] == {}
    assert not (stale_build / "report.pdf").exists()
    assert not (stale_build / "report.docx").exists()


def test_corrupt_status_json_recovers_explicitly(tmp_path: Path) -> None:
    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "job-status.json").write_text("{not-valid-json", encoding="utf-8")

    res = report_run.run_report_job(src, dest, "unsupported_claim")
    assert res["recovered_prior_state"] == "corrupt_status"
    assert res["state"] == "rejected"
    assert res["approval_ready"] is False
    assert res["artifacts"] == {}


def test_unwritable_destination_and_save_failure_report_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "unwritable"
    reported: list[dict[str, object]] = []

    def fail_save(path: Path, data: dict[str, object]) -> None:
        raise PermissionError("read-only destination")

    monkeypatch.setattr(report_run, "save_json", fail_save)
    res = report_run.run_report_job(src, dest, "missing_input", error_reporter=reported.append)
    assert res["state"] == "failed"
    assert res["persistence_failed"] is True
    assert res["approval_ready"] is False
    assert res["artifacts"] == {}
    assert len(reported) == 1
    assert reported[0]["persistence_failed"] is True


def test_hash_failure_prevents_awaiting_visual_review_and_clears_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "dest"

    def ok_runner(cmd: list[str], timeout_s: float) -> report_run.CommandResult:
        build = dest / "build"
        build.mkdir(exist_ok=True)
        (build / "report.pdf").write_bytes(b"%PDF-ok")
        (build / "report.docx").write_bytes(b"PK-ok")
        return report_run.CommandResult(returncode=0, stdout="ok", stderr="")

    def boom_hash(path: Path) -> str:
        raise OSError("disk read failure during hash")

    monkeypatch.setattr(report_run, "_sha256", boom_hash)
    res = report_run.run_report_job(src, dest, "refine", runner=ok_runner)
    assert res["state"] == "failed"
    assert res["error_type"] == "OSError"
    assert res["approval_ready"] is False
    assert res["artifacts"] == {}


def test_filesystem_copy_error_is_terminal_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "dest"

    def fail_copy(*args, **kwargs):
        raise shutil.Error("copy failed")

    monkeypatch.setattr(report_run.shutil, "copytree", fail_copy)
    res = report_run.run_report_job(src, dest, "refine")
    assert res["state"] == "failed"
    assert res["error_type"] == "Error"
    assert res["approval_ready"] is False
    assert res["artifacts"] == {}


def test_successful_build_records_hashes_with_approval_ready_false(
    tmp_path: Path,
) -> None:
    import report_run

    src = _make_synthetic_source(tmp_path)
    dest = tmp_path / "dest"

    def ok_runner(cmd: list[str], timeout_s: float) -> report_run.CommandResult:
        build = dest / "build"
        build.mkdir(exist_ok=True)
        (build / "report.pdf").write_bytes(b"%PDF-1.7\nok")
        (build / "report.docx").write_bytes(b"PK-ok")
        return report_run.CommandResult(returncode=0, stdout="ok", stderr="")

    res = report_run.run_report_job(src, dest, "refine", runner=ok_runner)
    assert res["state"] == "awaiting_visual_review"
    assert res["approval_ready"] is False
    assert set(res["artifacts"].keys()) == {"build/report.pdf", "build/report.docx"}
