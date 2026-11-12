"""STAFF_RESULT schema (RD #3619, RM epic #2560 slice 9)."""

from __future__ import annotations

import json

import pytest
from staff.staff_result import STAFF_RESULT_SCHEMA


@pytest.mark.unit
def test_schema_is_json_serializable() -> None:
    # The claude/codex CLIs take the schema inline or from a file; either way it
    # must round-trip through json.dumps without a custom encoder.
    assert json.loads(json.dumps(STAFF_RESULT_SCHEMA)) == STAFF_RESULT_SCHEMA


@pytest.mark.unit
def test_schema_requires_summary_and_forbids_unknown_fields() -> None:
    assert STAFF_RESULT_SCHEMA["type"] == "object"
    assert STAFF_RESULT_SCHEMA["required"] == ["summary"]
    assert STAFF_RESULT_SCHEMA["properties"]["summary"]["type"] == "string"
    assert STAFF_RESULT_SCHEMA["additionalProperties"] is False
