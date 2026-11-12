"""Retry eligibility, backoff and provider fallback selection (500-line cap split)."""

from __future__ import annotations

import os
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from staff.budget import BudgetGuard
    from staff.roles import RoleSpec
    from staff.store import RunRecord

RETRYABLE_FAILURE_CLASSES = frozenset(
    {
        "rate_limited",
        "provider_capacity",
        "quota_exhausted",
        "provider_error",
        "workspace_error",
    }
)

NON_RETRYABLE_FAILURE_CLASSES = frozenset(
    {
        "needs_input",
        "auth_expired",
        "lease_blocked",
        "cli_missing",
        "cli_outdated",
        "config",
        "orphaned",
        "unkillable",
        "timeout",
        "stalled",
        "unknown",
        "no_result",
    }
)

DEFAULT_BASE_BACKOFF_SECONDS = float(os.environ.get("STAFF_RETRY_BASE_SECONDS", "2.0"))
DEFAULT_MAX_BACKOFF_SECONDS = float(os.environ.get("STAFF_RETRY_MAX_SECONDS", "300.0"))


def compute_backoff(
    attempt: int,
    base_seconds: float | None = None,
    max_seconds: float | None = None,
    jitter: bool = True,
) -> float:
    """Calculate exponential backoff with optional jitter."""
    base = base_seconds if base_seconds is not None else float(os.environ.get("STAFF_RETRY_BASE_SECONDS", "2.0"))
    cap = max_seconds if max_seconds is not None else float(os.environ.get("STAFF_RETRY_MAX_SECONDS", "300.0"))
    if base <= 0:
        return 0.0

    exp = min(cap, base * (2.0 ** max(0, attempt - 1)))
    jitter_val = random.uniform(0.0, min(1.0, exp * 0.25)) if jitter else 0.0  # noqa: S311
    return float(min(cap, exp + jitter_val))


def compute_next_attempt_at(delay_seconds: float) -> str:
    """Return UTC ISO-8601 string for next attempt after delay_seconds."""
    next_time = datetime.now(UTC) + timedelta(seconds=max(0.0, delay_seconds))
    return next_time.isoformat().replace("+00:00", "Z")


def is_retryable_class(failure_class: str) -> bool:
    """Return True if failure_class is eligible for bounded retry."""
    return failure_class in RETRYABLE_FAILURE_CLASSES and failure_class not in NON_RETRYABLE_FAILURE_CLASSES


def should_retry(
    rec: RunRecord,
    role: RoleSpec,
    budget_guard: BudgetGuard | None = None,
) -> tuple[bool, str]:
    """Determine whether a failed run is eligible to retry on the same provider."""
    if not is_retryable_class(rec.failure_class):
        return False, f"failure class '{rec.failure_class}' is not retryable"

    if rec.failure_class == "provider_capacity":
        return False, "provider capacity uses declarative fallback, not same-provider retry"
    if rec.failure_class == "quota_exhausted":
        return False, f"{rec.failure_class} uses failover chain, not same-provider retry"

    max_att = getattr(rec, "max_attempts", 2) or getattr(role, "max_attempts", 2) or 2
    if rec.attempt >= max_att:
        return False, f"retries exhausted ({rec.attempt}/{max_att})"

    if budget_guard is not None:
        can_run, reason = budget_guard.can_run(role)
        if not can_run:
            return False, f"daily budget reached: {reason}"

    return True, "retry allowed"


def next_fallback_provider(
    role: RoleSpec,
    current_provider: str,
    usable: Callable[[str], bool] = lambda _pid: True,
) -> str | None:
    """Return the next usable provider in the role's fallback chain, or None if exhausted."""
    fallback_chain = getattr(role, "fallback_providers", ())
    if fallback_chain:
        for p in fallback_chain:
            if p != current_provider and usable(p):
                return p
        return None

    providers = list(role.providers)
    if current_provider in providers:
        for p in providers[providers.index(current_provider) + 1 :]:
            if usable(p):
                return p

    return None
