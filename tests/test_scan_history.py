import json

from scripts.scan_history import findings_summary, render_report, validate_report_path, write_private_report


def test_findings_summary_keeps_only_safe_references() -> None:
    raw = json.dumps(
        [
            {
                "RuleID": "fake-rule",
                "File": "src/example.py",
                "Commit": "abc123",
                "StartLine": 17,
                "PrivateValue": "PRIVATE_SENTINEL_MARKER_48291",
                "Match": "PRIVATE_SENTINEL_MARKER_48291",
            }
        ]
    )

    summary = findings_summary(raw)

    assert summary == [{"rule": "fake-rule", "file": "src/example.py", "commit": "abc123", "line": 17}]
    assert "PRIVATE_SENTINEL_MARKER_48291" not in json.dumps(summary)


def test_report_path_must_be_outside_repository(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    outside = private_dir / "report.json"

    assert validate_report_path(outside, repo) == outside.resolve()

    try:
        validate_report_path(repo / "report.json", repo)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("report path inside the repository was accepted")


def test_report_has_required_inventory_sections_and_no_scanner_values() -> None:
    report = render_report(
        findings_summary(
            '[{"RuleID":"fake-rule","File":"src/example.py","Commit":"abc123",'
            '"StartLine":17,"PrivateValue":"PRIVATE_SENTINEL_MARKER_48291"}]'
        ),
        [],
        {
            "actions_artifacts": 2,
            "actions_runs": 3,
            "forks": 4,
            "issue_and_pr_comments": 5,
            "pull_request_review_comments": 6,
        },
    )

    assert all(
        section in report
        for section in ("Tree scan", "Full history scan", "Actions", "Forks", "discussions", "Rotation list")
    )
    assert "abc123" in report
    assert "PRIVATE_SENTINEL_MARKER_48291" not in report


def test_private_report_is_created_with_restricted_permissions(tmp_path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    report = private_dir / "inventory.md"

    write_private_report(report, "safe references only")

    assert report.read_text() == "safe references only"
    assert report.stat().st_mode & 0o777 == 0o600
