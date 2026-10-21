"""Secret redaction for assistant memory entries."""

from __future__ import annotations

import re

from staff.redaction import redact_sensitive_content

_GHS_TOKEN_RE = re.compile(r"\bghs_[A-Za-z0-9_]{20,254}\b")


def redact_memory_text(text: str) -> str:
    """Redact secrets before persisting memory bodies (includes GitHub App tokens)."""
    cleaned = redact_sensitive_content(text)
    return _GHS_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", cleaned)
