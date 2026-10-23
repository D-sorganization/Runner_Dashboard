"""Shared bounded claim/quotation checks for immutable reference evidence.

This is conservative lexical correspondence, not semantic entailment: substantive
claim words and polarity markers must co-occur in one evidence sentence.
"""

import re

_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_REFERENCE_TOKEN = re.compile(r"\[ref:[^]\r\n]*\]|lib://[^\s]+")
_QUOTE = re.compile(r'["“]([^"”]{1,200})["”]')
_CONTRACTION = re.compile(r"\b([a-z]+)n't\b")

# Remove grammatical glue only. Modality, quantities, and negation remain evidence.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "hers",
        "him",
        "his",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "ours",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "we",
        "were",
        "which",
        "who",
        "with",
        "you",
        "your",
    }
)
_NEGATIONS = frozenset(
    {
        "cannot",
        "exclude",
        "excluded",
        "excludes",
        "excluding",
        "fail",
        "failed",
        "fails",
        "lack",
        "lacking",
        "lacks",
        "missing",
        "neither",
        "never",
        "no",
        "none",
        "nor",
        "not",
        "unable",
        "without",
    }
)


def _tokens(text: str) -> set[str]:
    """Return case-folded lexical terms, expanding common negative contractions."""
    normalized = text.casefold().replace("’", "'")
    normalized = normalized.replace("can't", "can not").replace("cannot", "can not")
    normalized = normalized.replace("won't", "will not").replace("shan't", "shall not")
    normalized = _CONTRACTION.sub(r"\1 not", normalized)
    return set(_WORD.findall(normalized))


def _claim_terms(text: str) -> set[str]:
    return _tokens(text) - _STOP_WORDS


def _sentence_chunks(text: str) -> list[str]:
    return [sentence.strip() for sentence in _SENTENCE.split(text) if sentence.strip()]


def claim_is_supported(content: str, offset: int, evidence: str) -> bool:
    """Check preceding bounded sentence for conservative lexical correspondence."""
    prefix = content[max(0, offset - 500) : offset].rstrip()
    prefix = _REFERENCE_TOKEN.sub(" ", prefix)
    if prefix.endswith((".", "!", "?")):
        prefix = prefix[:-1]
    claim = re.split(r"[.!?\n]", prefix)[-1].strip()
    if not claim:
        return True

    terms = _claim_terms(claim)
    if not terms:
        return False
    claim_negations = terms & _NEGATIONS
    quotes = _QUOTE.findall(claim)

    for sentence in _sentence_chunks(evidence):
        sentence_terms = _claim_terms(sentence)
        if not terms <= sentence_terms:
            continue
        if claim_negations != sentence_terms & _NEGATIONS:
            continue
        if any(quote.casefold() not in sentence.casefold() for quote in quotes):
            continue
        return True
    return False
