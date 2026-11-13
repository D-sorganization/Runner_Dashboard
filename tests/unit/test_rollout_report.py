"""Playbook rollout reporting: one-line Barbara PR summaries, daily RM tally, to:grok escalation (#3490)."""

from __future__ import annotations

from typing import Any

from scripts import rollout_report as rr

OFF: dict[str, str] = {}
ON = {"ROLLOUT_REPORT_LIVE": "on"}


def _assignment(state: str) -> dict[str, Any]:
    return {"id": 1, "state": state}


def test_rollout_report_live_is_off_by_default_and_requires_exact_on() -> None:
    assert rr.rollout_report_live(OFF) is False
    assert rr.rollout_report_live({"ROLLOUT_REPORT_LIVE": "On"}) is True
    assert rr.rollout_report_live({"ROLLOUT_REPORT_LIVE": "yes"}) is False


def test_format_one_line_summary_collapses_newlines_and_whitespace() -> None:
    assert rr.format_one_line_summary("line one\n  line two\t\tline three") == "line one line two line three"


def test_format_one_line_summary_truncates_long_text() -> None:
    long_text = "x" * 600
    out = rr.format_one_line_summary(long_text, max_len=500)
    assert len(out) == 500 and out.endswith("…")


def test_format_daily_tally_zero_fills_every_state_in_enum_order() -> None:
    assignments = [_assignment("RUNNING"), _assignment("RUNNING"), _assignment("COMPLETED")]
    out = rr.format_daily_tally(assignments, as_of="2026-10-29")
    assert out == (
        "Playbook rollout tally 2026-10-29: ASSIGNED 0, ACKNOWLEDGED 0, RUNNING 2, COMPLETED 1, "
        "FAILED 0, TIMED_OUT 0, ORPHANED 0 (total 3)"
    )


def test_format_daily_tally_ignores_unknown_or_missing_states() -> None:
    assignments = [_assignment("BOGUS"), {"id": 2}, _assignment("FAILED")]
    out = rr.format_daily_tally(assignments, as_of="2026-10-29")
    assert "FAILED 1" in out and "(total 3)" in out


def test_format_daily_tally_handles_empty_assignments() -> None:
    out = rr.format_daily_tally([], as_of="2026-10-29")
    assert out.endswith("(total 0)") and "ASSIGNED 0" in out


def test_build_report_body_plain() -> None:
    assert rr.build_report_body("a one-liner", escalate_to_grok=False) == "a one-liner"


def test_build_report_body_appends_escalation_marker() -> None:
    body = rr.build_report_body("a one-liner", escalate_to_grok=True)
    assert body.startswith("a one-liner\n\n") and rr.ESCALATION_MARKER in body


def test_post_report_dry_run_by_default_never_calls_poster_or_labeler() -> None:
    calls: list[str] = []
    result = rr.post_report(
        "D-sorganization/Barbara",
        69,
        "body text",
        live=False,
        escalate_to_grok=True,
        poster=lambda *a: calls.append("poster") or {},
        labeler=lambda *a: calls.append("labeler") or {},
    )
    assert calls == []
    assert result == {
        "ok": True,
        "status": "dry_run",
        "repo": "D-sorganization/Barbara",
        "number": 69,
        "body": "body text",
        "escalation_label": rr.ESCALATION_LABEL,
    }


def test_post_report_live_posts_then_labels() -> None:
    calls: list[tuple[str, ...]] = []

    def poster(repo: str, number: int, body: str) -> dict[str, Any]:
        calls.append(("post", repo, str(number), body))
        return {"html_url": "https://example/comment/1"}

    def labeler(repo: str, number: int, label: str) -> dict[str, Any]:
        calls.append(("label", repo, str(number), label))
        return {}

    result = rr.post_report(
        "D-sorganization/Barbara", 69, "body text", live=True, escalate_to_grok=True, poster=poster, labeler=labeler
    )
    assert calls == [
        ("post", "D-sorganization/Barbara", "69", "body text"),
        ("label", "D-sorganization/Barbara", "69", rr.ESCALATION_LABEL),
    ]
    assert result == {
        "ok": True,
        "status": "posted",
        "comment_url": "https://example/comment/1",
        "escalation": "labeled",
    }


def test_post_report_live_without_escalation_skips_labeler() -> None:
    labeler_calls: list[str] = []
    result = rr.post_report(
        "D-sorganization/Barbara",
        69,
        "body text",
        live=True,
        escalate_to_grok=False,
        poster=lambda *a: {"html_url": "u"},
        labeler=lambda *a: labeler_calls.append("called") or {},
    )
    assert labeler_calls == []
    assert result == {"ok": True, "status": "posted", "comment_url": "u"}


def test_post_report_live_poster_failure_is_fail_closed_never_raises() -> None:
    def poster(*_a: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    result = rr.post_report(
        "owner/repo", 1, "body", live=True, escalate_to_grok=False, poster=poster, labeler=lambda *a: {}
    )
    assert result == {"ok": False, "status": "not_posted", "error": "RuntimeError"}


def test_post_report_live_label_failure_keeps_the_posted_comment_result() -> None:
    def labeler(*_a: Any) -> dict[str, Any]:
        raise RuntimeError("label boom")

    result = rr.post_report(
        "owner/repo", 1, "body", live=True, escalate_to_grok=True, poster=lambda *a: {"html_url": "u"}, labeler=labeler
    )
    assert result == {"ok": True, "status": "posted", "comment_url": "u", "escalation": "label_failed:RuntimeError"}


def test_main_summary_dry_run_prints_ok_json(capsys: Any) -> None:
    rc = rr.main(["summary", "--repo", "D-sorganization/Barbara", "--number", "69", "--text", "line1\nline2"], env=OFF)
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "dry_run"' in out and "line1 line2" in out


def test_main_tally_reads_assignments_file(tmp_path: Any, capsys: Any) -> None:
    path = tmp_path / "assignments.json"
    path.write_text('[{"state": "RUNNING"}, {"state": "COMPLETED"}]', encoding="utf-8")
    rc = rr.main(
        [
            "tally",
            "--repo",
            "D-sorganization/Repository_Management",
            "--number",
            "2376",
            "--assignments",
            str(path),
            "--as-of",
            "2026-10-29",
        ],
        env=OFF,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "RUNNING 1" in out and "COMPLETED 1" in out


def test_main_tally_rejects_non_list_assignments(tmp_path: Any) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    rc = rr.main(["tally", "--repo", "owner/repo", "--number", "1", "--assignments", str(path)], env=OFF)
    assert rc == 1
