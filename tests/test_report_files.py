"""Unit tests for backend/report_files.py (issue #155)."""

from __future__ import annotations

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import report_files  # noqa: E402


class TestSanitizeReportDate:
    def test_keeps_digits_and_dashes(self) -> None:
        assert report_files.sanitize_report_date("2024-03-15") == "2024-03-15"

    def test_removes_slashes(self) -> None:
        assert report_files.sanitize_report_date("2024/03/15") == "20240315"

    def test_removes_spaces(self) -> None:
        assert report_files.sanitize_report_date("2024 03 15") == "20240315"

    def test_removes_letters(self) -> None:
        assert report_files.sanitize_report_date("report-2024-03-15") == "-2024-03-15"

    def test_empty_string(self) -> None:
        assert report_files.sanitize_report_date("") == ""

    def test_mixed_garbage(self) -> None:
        assert report_files.sanitize_report_date("abc!@#2024-03-15xyz") == "2024-03-15"


class TestParseReportMetrics:
    def test_empty_content(self) -> None:
        assert report_files.parse_report_metrics("") == {}

    def test_single_metric_row(self) -> None:
        content = "| Metric | Value | Delta | Notes |\n| Open Issues | 42 | +3 | |"
        result = report_files.parse_report_metrics(content)
        assert result == {"Open Issues": {"value": "42", "delta": "+3"}}

    def test_multiple_metric_rows(self) -> None:
        content = """| Metric | Value | Delta | Notes |
| Open Issues | 42 | +3 | |
| Closed Issues | 10 | -2 | |"""
        result = report_files.parse_report_metrics(content)
        assert result == {
            "Open Issues": {"value": "42", "delta": "+3"},
            "Closed Issues": {"value": "10", "delta": "-2"},
        }

    def test_skips_header_and_separator(self) -> None:
        content = """| Metric | Value | Delta | Notes |
| --- | --- | --- | --- |
| Open Issues | 42 | +3 | |"""
        result = report_files.parse_report_metrics(content)
        assert result == {"Open Issues": {"value": "42", "delta": "+3"}}

    def test_ignores_non_matching_lines(self) -> None:
        content = "This is just a sentence.\n| Open Issues | 42 | +3 | |\nAnother sentence."
        result = report_files.parse_report_metrics(content)
        assert result == {"Open Issues": {"value": "42", "delta": "+3"}}

    def test_extra_whitespace_trimmed(self) -> None:
        content = "|  Open Issues   |   42   |   +3   |   |"
        result = report_files.parse_report_metrics(content)
        assert result == {"Open Issues": {"value": "42", "delta": "+3"}}

    def test_empty_delta(self) -> None:
        content = "| Open Issues | 42 | | |"
        result = report_files.parse_report_metrics(content)
        assert result == {"Open Issues": {"value": "42", "delta": ""}}

    def test_metric_named_metric_skipped(self) -> None:
        content = "| Metric | Value | Delta | Notes |"
        result = report_files.parse_report_metrics(content)
        assert result == {}

    def test_separator_row_skipped(self) -> None:
        content = "| --- | --- | --- | --- |"
        result = report_files.parse_report_metrics(content)
        assert result == {}

    def test_no_leading_pipe_no_match(self) -> None:
        content = "Open Issues | 42 | +3 |"
        result = report_files.parse_report_metrics(content)
        assert result == {}

    def test_ignores_extra_column_content(self) -> None:
        content = "| Open Issues | 42 | +3 | some notes here |"
        result = report_files.parse_report_metrics(content)
        assert result == {"Open Issues": {"value": "42", "delta": "+3"}}
