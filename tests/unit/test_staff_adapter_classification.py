"""Per-adapter failure classification fixtures (#2047 slice 1)."""

from __future__ import annotations

import pytest
from staff.adapter_failure import classify_adapter_output
from staff.classifier import classify_run_failure


@pytest.mark.parametrize(
    ("provider", "text", "expected"),
    [
        ("codex", "Error: Selected model is at capacity. Try again later.", "provider_capacity"),
        ("codex", "You have reached your usage limit for this billing period.", "quota_exhausted"),
        ("claude", "Error: 529 overloaded", "provider_capacity"),
        ("claude", "You have exceeded your maximum usage limit for Claude.", "quota_exhausted"),
        (
            "antigravity",
            "Error: RESOURCE_EXHAUSTED: quota exceeded for quota metric 'Queries'",
            "quota_exhausted",
        ),
        ("cursor-agent", "Usage limit reached for this account.", "quota_exhausted"),
        ("grok", "You have hit your usage limit for the Grok CLI.", "quota_exhausted"),
        ("grok", "Error: monthly quota exceeded for SuperGrok plan.", "quota_exhausted"),
        ("grok", "Error 429: rate limit exceeded, retry later.", "rate_limited"),
        ("grok", "Grok service is currently at capacity. Please try again soon.", "provider_capacity"),
        ("grok-cli", "Quota exhausted for this billing cycle.", "quota_exhausted"),
        ("grok_cli", "Rate limit reached; slow down.", "rate_limited"),
    ],
)
def test_adapter_classification_fixtures(provider: str, text: str, expected: str) -> None:
    hint = classify_adapter_output(provider, text)
    assert hint is not None
    assert hint.failure_class == expected
    diag = classify_run_failure(provider=provider, exit_code=1, output_text=text)
    assert diag.failure_class == expected


@pytest.mark.unit
def test_grok_auth_expired_classification() -> None:
    msg = "Error: Authentication expired. Please run grok login."
    diag = classify_run_failure(provider="grok", exit_code=1, output_text=msg)
    assert diag.failure_class == "auth_expired"
    assert "grok login" in diag.remediation
