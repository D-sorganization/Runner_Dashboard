"""Tests for HelpChatRequest pydantic model (issue #716)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.requests import HelpChatRequest
from pydantic import ValidationError


def test_empty_question_raises_validation_error():
    with pytest.raises(ValidationError):
        HelpChatRequest(question="")


def test_whitespace_only_question_raises():
    with pytest.raises(ValidationError):
        HelpChatRequest(question="   ")


def test_too_long_question_raises():
    with pytest.raises(ValidationError):
        HelpChatRequest(question="x" * 501)


def test_valid_question_accepted():
    req = HelpChatRequest(question="What is the status of the fleet?")
    assert req.question == "What is the status of the fleet?"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        HelpChatRequest(question="hello", extra_field="bad")


def test_question_at_max_length():
    req = HelpChatRequest(question="x" * 500)
    assert len(req.question) == 500


def test_question_strips_surrounding_whitespace():
    """str_strip_whitespace=True means leading/trailing spaces are stripped."""
    req = HelpChatRequest(question="  hello  ")
    assert req.question == "hello"
