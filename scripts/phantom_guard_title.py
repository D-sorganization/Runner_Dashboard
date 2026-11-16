#!/usr/bin/env python3
"""Detect PR titles that claim a product feature for anti-phantom Rule 2."""

from __future__ import annotations

import re
import sys

# Conventional-commit feature prefixes, or boastful one-word title openers only at
# the start of the title (not substrings like "subsystem" or "implement" mid-title).
_FEAT_PREFIX = re.compile(r"^feat(?:\(|:)", re.IGNORECASE)
_BOASTFUL_OPENER = re.compile(
    r"^(?:implement|system|engine|framework)\b",
    re.IGNORECASE,
)


def title_triggers_rule2(title: str) -> bool:
    """Return True when the title suggests a feature worth checking against the diff."""
    stripped = title.strip()
    if not stripped:
        return False
    return bool(_FEAT_PREFIX.match(stripped) or _BOASTFUL_OPENER.match(stripped))


def main() -> None:
    title = sys.stdin.read() if not sys.stdin.isatty() else (sys.argv[1] if len(sys.argv) > 1 else "")
    raise SystemExit(0 if title_triggers_rule2(title) else 1)


if __name__ == "__main__":
    main()
