"""Secret redaction for assistant memory entries."""

from __future__ import annotations

import re
from typing import Any

from staff import redaction
from staff.redaction import redact_sensitive_content

_GHS_TOKEN_RE = re.compile(r"\bghs_[A-Za-z0-9_]{20,254}\b")
_SECRET_PATTERNS = (*[getattr(redaction, k) for k in dir(redaction) if k.endswith("_RE")], _GHS_TOKEN_RE)


def contains_secret(val: Any) -> bool:
    if isinstance(val, str):
        return any(p.search(val) for p in _SECRET_PATTERNS)
    seq = val if isinstance(val, (list, tuple, set)) else val.values() if isinstance(val, dict) else ()
    return any(contains_secret(x) for x in seq)


def redact_memory_text(text: str) -> str:
    """Redact secrets before persisting memory bodies (includes GitHub App tokens)."""
    cleaned = redact_sensitive_content(text)
    return _GHS_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", cleaned)
