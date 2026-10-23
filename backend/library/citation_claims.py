"""Shared bounded claim/quotation checks for immutable reference evidence."""

import re

_WORD = re.compile(r"[a-z0-9]{3,}")


def claim_is_supported(content: str, offset: int, evidence: str) -> bool:
    """Check the preceding sentence without exposing the cited source text."""
    prefix = content[max(0, offset - 500) : offset].rstrip()
    if prefix.endswith((".", "!", "?")):
        prefix = prefix[:-1]
    claim = re.split(r"[.!?\n]", prefix)[-1]
    words = set(_WORD.findall(claim.lower()))
    source_words = set(_WORD.findall(evidence.lower()))
    quotes = re.findall(r"[\"“]([^\"”]{1,200})[\"”]", claim)
    return not any(quote.casefold() not in evidence.casefold() for quote in quotes) and (
        not words or len(words & source_words) / len(words) >= 0.2
    )
