"""Per-adapter capacity / quota / rate-limit classification from CLI output (#2047)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterFailureHint:
    failure_class: str
    detail: str = ""


def _generic_quota_hint(lower: str) -> AdapterFailureHint | None:
    quota_patterns = (
        "exceeded your current quota",
        "quota exhausted",
        "out of quota",
        "usage limit",
        "plan limit",
        "billing hard limit",
    )
    if "disk quota" in lower:
        return None
    if any(pat in lower for pat in quota_patterns):
        return AdapterFailureHint("quota_exhausted", "quota exhausted")
    return None


def adapter_failure_classification(provider: str, combined: str) -> tuple[str, bool, str, str] | None:
    """Return (failure_class, retryable, remediation, error) when adapter rules match."""
    lower = (combined or "").lower()
    hint = classify_adapter_output(provider, combined) or _generic_quota_hint(lower)
    if hint is None:
        return None
    if hint.failure_class == "rate_limited":
        return (
            "rate_limited",
            True,
            f"Provider {provider} rate limit reached; back off before retry.",
            hint.detail or f"Rate limit exceeded on {provider}",
        )
    return (
        hint.failure_class,
        True,
        (
            f"Provider {provider} reported {hint.failure_class}; "
            "the fleet failover chain will try the next allowed provider."
        ),
        hint.detail or hint.failure_class,
    )


def classify_adapter_output(provider: str, text: str) -> AdapterFailureHint | None:
    """Return a provider-specific failure class hint, or None to use generic rules."""
    pid = provider.lower().strip()
    lower = (text or "").lower()
    if not lower.strip():
        return None
    if pid == "codex":
        return _classify_codex(lower, text)
    if pid == "claude":
        return _classify_claude(lower, text)
    if pid in ("antigravity", "gemini"):
        return _classify_antigravity(lower, text)
    if pid == "cursor-agent":
        return _classify_cursor_agent(lower, text)
    return None


def _classify_codex(lower: str, text: str) -> AdapterFailureHint | None:
    if "selected model is at capacity" in lower or "model is at capacity" in lower:
        return AdapterFailureHint("provider_capacity", text.strip()[:300])
    if "usage limit" in lower or "plan limit" in lower or "billing hard limit" in lower:
        return AdapterFailureHint("quota_exhausted", text.strip()[:300])
    if "rate limit" in lower or "too many requests" in lower or "429" in lower:
        return AdapterFailureHint("rate_limited", text.strip()[:300])
    return None


def _classify_claude(lower: str, text: str) -> AdapterFailureHint | None:
    if "usage limit" in lower or "exceeded your maximum usage" in lower:
        return AdapterFailureHint("quota_exhausted", text.strip()[:300])
    if "529" in lower or "overloaded" in lower:
        return AdapterFailureHint("provider_capacity", text.strip()[:300])
    if "rate limit" in lower or "429" in lower:
        return AdapterFailureHint("rate_limited", text.strip()[:300])
    return None


def _classify_antigravity(lower: str, text: str) -> AdapterFailureHint | None:
    if "resource_exhausted" in lower and "quota" in lower:
        return AdapterFailureHint("quota_exhausted", text.strip()[:300])
    if "resource_exhausted" in lower:
        return AdapterFailureHint("rate_limited", text.strip()[:300])
    if "quota exceeded" in lower or "out of quota" in lower:
        return AdapterFailureHint("quota_exhausted", text.strip()[:300])
    if "at capacity" in lower:
        return AdapterFailureHint("provider_capacity", text.strip()[:300])
    if "429" in lower or "rate limit" in lower:
        return AdapterFailureHint("rate_limited", text.strip()[:300])
    return None


def _classify_cursor_agent(lower: str, text: str) -> AdapterFailureHint | None:
    if "usage limit" in lower or "usage-limit" in lower or "quota" in lower:
        return AdapterFailureHint("quota_exhausted", text.strip()[:300])
    if "rate limit" in lower or "429" in lower:
        return AdapterFailureHint("rate_limited", text.strip()[:300])
    return None


def parse_chain_summary(attempts: list[dict[str, str]]) -> str:
    """Human brief: 'Codex was out, ran on Claude'."""
    if not attempts:
        return ""
    failed = [a for a in attempts if a.get("outcome") != "succeeded"]
    if not failed:
        return ""
    last_fail = failed[-1]
    success = next((a for a in reversed(attempts) if a.get("outcome") == "succeeded"), None)
    prov = last_fail.get("provider", "?")
    if success and success.get("provider") != prov:
        return f"{prov} was unavailable ({last_fail.get('outcome', 'failed')}); ran on {success.get('provider')}"
    return f"{prov} unavailable ({last_fail.get('outcome', 'failed')})"
