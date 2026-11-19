"""Per-principal, per-role dispatch buckets on staff /run (#4457)."""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from identity import Principal
from staff import dispatch_rate_policy as drp
from staff.rate_limit import TokenBucketLimiter, check_rate_limit, reset_rate_limiter


@pytest.fixture(autouse=True)
def _fresh_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture(autouse=True)
def _fresh_dispatch_rate_policy() -> Iterator[None]:
    """Read the repo policy, not one another test left in the lru_cache (main-red from #4460)."""
    drp.reload_dispatch_rate_policy()
    yield
    drp.reload_dispatch_rate_policy()


def test_code_reviewer_and_ad_hoc_do_not_share_one_bucket() -> None:
    limiter = TokenBucketLimiter(capacity={"dispatches:code-reviewer": 1}, rate_per_sec={"dispatches:code-reviewer": 0.0})
    principal = Principal(id="agent-grok", type="bot", name="Grok", roles=[], scopes=[])
    check_rate_limit("dispatches", principal, limiter=limiter, role="code-reviewer")
    # second code-reviewer blocked
    with pytest.raises(HTTPException):
        check_rate_limit("dispatches", principal, limiter=limiter, role="code-reviewer")
    # ad-hoc uses a different action key
    limiter.set_limits("dispatches:ad-hoc", 1, 0.0)
    check_rate_limit("dispatches", principal, limiter=limiter, role="ad-hoc")


def test_distinct_principals_have_isolated_buckets_for_same_role() -> None:
    limiter = TokenBucketLimiter(capacity={"dispatches:code-reviewer": 1}, rate_per_sec={"dispatches:code-reviewer": 0.0})
    a = Principal(id="agent-a", type="bot", name="A", roles=[], scopes=[])
    b = Principal(id="agent-b", type="bot", name="B", roles=[], scopes=[])
    check_rate_limit("dispatches", a, limiter=limiter, role="code-reviewer")
    check_rate_limit("dispatches", b, limiter=limiter, role="code-reviewer")


def test_retry_after_header_is_consistent_on_role_dispatch_limit() -> None:
    limiter = TokenBucketLimiter(capacity={"dispatches:code-reviewer": 1}, rate_per_sec={"dispatches:code-reviewer": 1 / 3600})
    principal = Principal(id="p1", type="bot", name="P", roles=[], scopes=[])
    check_rate_limit("dispatches", principal, limiter=limiter, role="code-reviewer")
    with pytest.raises(HTTPException) as exc_info:
        check_rate_limit("dispatches", principal, limiter=limiter, role="code-reviewer")
    assert exc_info.value.status_code == 429
    retry_hdr = exc_info.value.headers.get("Retry-After")
    assert retry_hdr is not None and int(retry_hdr) >= 1
    detail = exc_info.value.detail
    assert detail["code"] == "rate_limited"
    assert detail["retry_after"] == pytest.approx(float(retry_hdr), rel=0, abs=1)
    assert math.isfinite(detail["retry_after"])


def test_privileged_principal_exempt_from_policy_role_dispatch_bucket() -> None:
    """Owner / loopback sessions keep elevated dispatch budget; bots stay per-role capped (#4460 B1)."""
    limiter = TokenBucketLimiter()
    owner = Principal(id="owner-session", type="human", name="Owner", roles=[], scopes=[])
    loopback = Principal(id="__loopback__", type="human", name="Loopback", roles=[], scopes=[])
    for principal in (owner, loopback):
        for _ in range(11):
            check_rate_limit("dispatches", principal, limiter=limiter, role="ad-hoc")


def test_agent_principal_hits_code_reviewer_hourly_cap() -> None:
    limiter = TokenBucketLimiter()
    agent = Principal(id="agent-cursor", type="bot", name="Cursor", roles=[], scopes=[])
    for _ in range(30):
        check_rate_limit("dispatches", agent, limiter=limiter, role="code-reviewer")
    with pytest.raises(HTTPException) as exc_info:
        check_rate_limit("dispatches", agent, limiter=limiter, role="code-reviewer")
    assert exc_info.value.status_code == 429
