"""Helpers for daily progress report files."""

from __future__ import annotations

import re

_REPORT_TABLE_ROW = re.compile(
    r"\|\s*(.+?)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]*?)\s*\|"
)


def sanitize_report_date(raw_date: str) -> str:
    """Keep only digits and dashes from a report date path segment."""
    return "".join(c for c in raw_date if c in "0123456789-")


def parse_report_metrics(content: str) -> dict:
    """Extract key metrics from a daily progress report markdown."""
    metrics = {}
    for line in content.split("\n"):
        match = _REPORT_TABLE_ROW.match(line)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            delta = match.group(3).strip()
            if key and key != "Metric" and key != "---":
                metrics[key] = {"value": value, "delta": delta}
    return metrics
