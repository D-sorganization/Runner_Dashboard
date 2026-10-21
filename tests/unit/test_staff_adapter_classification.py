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
    ],
)
def test_adapter_classification_fixtures(provider: str, text: str, expected: str) -> None:
    hint = classify_adapter_output(provider, text)
    assert hint is not None
    assert hint.failure_class == expected
    diag = classify_run_failure(provider=provider, exit_code=1, output_text=text)
    assert diag.failure_class == expected
