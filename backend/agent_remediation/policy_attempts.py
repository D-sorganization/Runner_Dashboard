"""Attempt-window helpers for remediation policy (extracted for file-size cap)."""

from __future__ import annotations

import datetime as _dt_mod
from datetime import datetime

from agent_remediation.policy import UTC, AttemptRecord


def _parse_timestamp(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _attempts_for_fingerprint(
    fingerprint: str,
    attempts: list[AttemptRecord],
    *,
    window_hours: int,
) -> list[AttemptRecord]:
    cutoff = datetime.now(UTC) - _dt_mod.timedelta(hours=window_hours)
    result = []
    for a in attempts:
        if a.fingerprint != fingerprint:
            continue
        ts = _parse_timestamp(a.created_at)
        if ts is None or ts >= cutoff:
            result.append(a)
    return result


def _attempts_for_provider(
    fingerprint: str,
    provider_id: str,
    attempts: list[AttemptRecord],
    *,
    window_hours: int,
) -> list[AttemptRecord]:
    return [
        a
        for a in _attempts_for_fingerprint(fingerprint, attempts, window_hours=window_hours)
        if a.provider_id == provider_id
    ]
