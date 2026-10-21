"""Fail-closed validation for external Actions job payloads."""

from __future__ import annotations

from typing import Any

from job_metrics import positive_attempt
from models.github_payloads import GhJob
from pydantic import ValidationError


def positive_identifier(value: Any) -> int | None:
    """Accept only positive JSON integer IDs; never coerce booleans or floats."""
    return value if type(value) is int and value > 0 else None


def _job_labels(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    labels = []
    for label in value:
        if isinstance(label, str):
            labels.append(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            labels.append(label["name"])
        else:
            return None
    return labels


def validated_job_payload(job: dict[str, Any], run_id: int) -> dict[str, Any] | None:
    """Normalize labels and strictly validate the declared dashboard job model."""
    job_id = positive_identifier(job.get("id"))
    labels = _job_labels(job.get("labels", []))
    if job_id is None or labels is None:
        return None
    attempt = positive_attempt(job.get("run_attempt"))
    try:
        parsed = GhJob.model_validate(
            {**job, "id": job_id, "run_id": run_id, "run_attempt": attempt, "labels": labels},
            strict=True,
        )
    except ValidationError:
        return None
    return parsed.model_dump()
