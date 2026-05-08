"""Tests for backend/scheduled_workflows.py — issue #386."""

from __future__ import annotations

import scheduled_workflows as sw

# ---------------------------------------------------------------------------
# extract_cron_expressions
# ---------------------------------------------------------------------------


SIMPLE_CRON_YAML = """\
name: Nightly CI
on:
  schedule:
    - cron: '0 2 * * *'
  push:
    branches: [main]
"""

MULTI_CRON_YAML = """\
name: Multi Schedule
on:
  schedule:
    - cron: '0 1 * * 1'
    - cron: '30 6 * * *'
"""

NO_CRON_YAML = """\
name: Push Only
on:
  push:
    branches: [main]
"""

CRON_WITH_COMMENT_YAML = """\
name: With Comment
on:
  schedule:
    - cron: '0 3 * * *'  # runs daily at 3am UTC
"""


def test_extract_cron_single() -> None:
    result = sw.extract_cron_expressions(SIMPLE_CRON_YAML)
    assert result == ["0 2 * * *"]


def test_extract_cron_multiple() -> None:
    result = sw.extract_cron_expressions(MULTI_CRON_YAML)
    assert "0 1 * * 1" in result
    assert "30 6 * * *" in result
    assert len(result) == 2


def test_extract_cron_no_schedule() -> None:
    result = sw.extract_cron_expressions(NO_CRON_YAML)
    assert result == []


def test_extract_cron_empty_string() -> None:
    result = sw.extract_cron_expressions("")
    assert result == []


def test_extract_cron_strips_inline_comment() -> None:
    result = sw.extract_cron_expressions(CRON_WITH_COMMENT_YAML)
    assert result == ["0 3 * * *"]


def test_extract_cron_deduplicates() -> None:
    yaml = """\
on:
  schedule:
    - cron: '0 1 * * *'
    - cron: '0 1 * * *'
"""
    result = sw.extract_cron_expressions(yaml)
    assert result.count("0 1 * * *") == 1
