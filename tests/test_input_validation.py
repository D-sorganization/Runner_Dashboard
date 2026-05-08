"""Tests for backend/input_validation.py — issue #386."""

from __future__ import annotations

import input_validation as iv
import pytest
from fastapi import HTTPException


def test_validate_workflow_inputs_none_returns_empty() -> None:
    assert iv.validate_workflow_inputs(None) == {}


def test_validate_workflow_inputs_empty_dict() -> None:
    assert iv.validate_workflow_inputs({}) == {}


def test_validate_workflow_inputs_string_value() -> None:
    result = iv.validate_workflow_inputs({"branch": "main"})
    assert result == {"branch": "main"}


def test_validate_workflow_inputs_bool_coerced() -> None:
    result = iv.validate_workflow_inputs({"debug": True})
    assert result["debug"] in ("True", "true", "1")


def test_validate_workflow_inputs_int_coerced() -> None:
    result = iv.validate_workflow_inputs({"count": 3})
    assert result["count"] == "3"


def test_validate_workflow_inputs_too_many_keys_raises() -> None:
    inputs = {f"key{i}": "v" for i in range(iv.MAX_INPUT_KEYS + 1)}
    with pytest.raises(HTTPException) as exc_info:
        iv.validate_workflow_inputs(inputs)
    # May raise 400 or 422 depending on implementation choice
    assert exc_info.value.status_code in (400, 422)


def test_validate_workflow_inputs_value_too_long_raises() -> None:
    inputs = {"big": "x" * (iv.MAX_INPUT_VALUE_LENGTH + 1)}
    with pytest.raises(HTTPException) as exc_info:
        iv.validate_workflow_inputs(inputs)
    assert exc_info.value.status_code in (400, 422)


def test_validate_workflow_inputs_none_value_raises() -> None:
    with pytest.raises(HTTPException):
        iv.validate_workflow_inputs({"key": None})


def test_validate_workflow_inputs_non_mapping_raises() -> None:
    with pytest.raises((HTTPException, TypeError, ValueError)):
        iv.validate_workflow_inputs(["not", "a", "dict"])
