from __future__ import annotations  # noqa: E402

import report_files  # noqa: E402

# ---------------------------------------------------------------------------
# sanitize_report_date
# ---------------------------------------------------------------------------


def test_sanitize_report_date_empty() -> None:
    assert report_files.sanitize_report_date("") == ""


def test_sanitize_report_date_already_clean() -> None:
    assert report_files.sanitize_report_date("2026-04-15") == "2026-04-15"


def test_sanitize_report_date_removes_slashes() -> None:
    assert report_files.sanitize_report_date("2026/04/15") == "20260415"


def test_sanitize_report_date_removes_spaces() -> None:
    assert report_files.sanitize_report_date("2026 04 15") == "20260415"


def test_sanitize_report_date_removes_dots() -> None:
    assert report_files.sanitize_report_date("2026.04.15") == "20260415"


def test_sanitize_report_date_removes_multiple_junk() -> None:
    assert report_files.sanitize_report_date("foo 2026-04-15 bar!") == "2026-04-15"


def test_sanitize_report_date_unicode() -> None:
    assert report_files.sanitize_report_date("2026年04月15日") == "20260415"


# ---------------------------------------------------------------------------
# parse_report_metrics
# ---------------------------------------------------------------------------


def test_parse_report_metrics_empty() -> None:
    assert report_files.parse_report_metrics("") == {}


def test_parse_report_metrics_no_table() -> None:
    content = "This is a plain markdown file with no table."
    assert report_files.parse_report_metrics(content) == {}


def test_parse_report_metrics_single_row() -> None:
    content = "| Metric | Value | Delta | Note |\n| Test Score | 85 | +5 | ok |"
    result = report_files.parse_report_metrics(content)
    assert result == {"Test Score": {"value": "85", "delta": "+5"}}


def test_parse_report_metrics_multiple_rows() -> None:
    content = """\
| Metric | Value | Delta | Note |
| Passing | 42 | +3 | |
| Failing | 5 | -2 | |
| Skipped | 8 | 0 | |
"""
    result = report_files.parse_report_metrics(content)
    assert result == {
        "Passing": {"value": "42", "delta": "+3"},
        "Failing": {"value": "5", "delta": "-2"},
        "Skipped": {"value": "8", "delta": "0"},
    }


def test_parse_report_metrics_ignores_header_separator() -> None:
    content = """\
| Metric | Value | Delta | Note |
| --- | --- | --- | --- |
| Coverage | 92% | +1% | |
"""
    result = report_files.parse_report_metrics(content)
    assert "Metric" not in result
    assert "---" not in result
    assert result == {"Coverage": {"value": "92%", "delta": "+1%"}}


def test_parse_report_metrics_ignores_empty_key() -> None:
    content = "| | 1 | 2 | 3 |"
    assert report_files.parse_report_metrics(content) == {}


def test_parse_report_metrics_mixed_content() -> None:
    content = """\
# Daily Report

Some intro text.

| Metric | Value | Delta | Note |
| --- | --- | --- | --- |
| Builds | 10 | +2 | |
| Errors | 1 | -1 | |

Footer text.
"""
    result = report_files.parse_report_metrics(content)
    assert result == {
        "Builds": {"value": "10", "delta": "+2"},
        "Errors": {"value": "1", "delta": "-1"},
    }


def test_parse_report_metrics_extra_whitespace() -> None:
    content = "|  Metric   |   Value   |  Delta  | Note |\n|   Score   |   100   |   +10   |  |"
    result = report_files.parse_report_metrics(content)
    assert result == {"Score": {"value": "100", "delta": "+10"}}
