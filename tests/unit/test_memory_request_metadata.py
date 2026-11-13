"""Typed memory write metadata must reject malformed lesson provenance."""

from __future__ import annotations

import pytest
from memory.models import MemoryAppendRequest, MemoryEntry, MemoryReviewRequest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "metadata",
    [
        {"repo": 12},
        {"repo": "   "},
        {"repo": "x" * 201},
        {"source_run": ["run-own"]},
        {"source_run": "run/other"},
        {"source_run": "r" * 121},
        {"review_status": "trusted"},
        {"visibility": "public"},
        {"body": {"text": "bounded", "repo": {"untrusted": "shape"}}},
    ],
)
def test_malformed_lesson_metadata_is_rejected(metadata: dict[str, object]) -> None:
    payload = {"entry_type": "lesson", "source_product": "test", "body": {"text": "bounded"}, **metadata}
    with pytest.raises(ValidationError):
        MemoryAppendRequest.model_validate(payload)


def test_supported_metadata_is_typed_and_body_repo_fallback_is_preserved() -> None:
    request = MemoryAppendRequest.model_validate(
        {
            "entry_type": "lesson",
            "source_product": "test",
            "body": {"text": "bounded", "repo": " UpstreamDrift "},
            "source_run": "run-own",
            "review_status": "proposed",
            "visibility": "barb-only",
        }
    )
    assert request.repo == "UpstreamDrift"
    assert request.source_run == "run-own"
    assert request.visibility == "barb-only"
    schema = MemoryAppendRequest.model_json_schema()["properties"]
    assert {"repo", "source_run", "review_status", "visibility"} <= schema.keys()


def test_review_target_is_a_supported_lifecycle_state() -> None:
    with pytest.raises(ValidationError):
        MemoryReviewRequest(review_status="trusted")
    assert MemoryReviewRequest(review_status="accepted").review_status == "accepted"


def test_legacy_entry_metadata_remains_readable() -> None:
    entry = MemoryEntry(
        id="legacy",
        entry_type="lesson",
        author_principal="legacy",
        source_product="test",
        body={"text": "old"},
        created_at="2026-01-01T00:00:00+00:00",
        visibility="historical-visibility",
        review_status="historical-state",
        source_run="legacy/run",
    )
    assert entry.visibility == "historical-visibility"
