"""Deny-by-default redaction gate for shared output surfaces (#3095, private-roles slice 2).

Scope (epic #3093): every shared surface -- board posts, turnover, inventory,
panel transcripts, logs and run history -- withholds content that originates
from a private role (#3094) unless that content was *explicitly* classified
shareable. A classification tag is a convenience for producers to state that
level; it is never the safety boundary itself (an absent, malformed, or
unparseable tag fails closed, it does not default to shareable).

Design: a producer wraps private-role output in an explicit classification
span, ``tag(value, level)``, before handing it to any shared-surface writer.
``gate_text`` is the single place every surface resolves those spans (LoD/DRY):
only a span tagged exactly :data:`private_mode.Level.PUBLIC` passes through
unchanged; every other level -- and any malformed span -- is replaced by a
fixed placeholder that never contains the original content, regardless of
encoding, quoting, or length. Text with no span is untouched (this gate only
acts on explicitly-tagged private-role content; plain text keeps going through
the existing secret-pattern redactor in ``staff.redaction``).

Wired into ``staff.redaction.redact_sensitive_content`` (called at every
existing shared-surface write boundary: conversation threads/messages/
proposals, run records and events, transcript streaming), so every surface
gets deny-by-default classification handling from one integration point.
"""

from __future__ import annotations

import re

from private_mode import Level

PLACEHOLDER = "[private content withheld]"
MALFORMED_PLACEHOLDER = "[private content withheld: malformed classification tag]"

_TAG_PREFIX = "<<PRIVATE:"
_TAG_SUFFIX = ">>"
_CLOSE_TAG = "<<END_PRIVATE>>"
_OPEN_RE = re.compile(re.escape(_TAG_PREFIX) + r"([A-Za-z_]*)" + re.escape(_TAG_SUFFIX))


class MalformedClassification(ValueError):
    """Raised by :func:`tag` when the classification input itself is invalid (DbC).

    A rejection here, at the tagging boundary, is the point: this gate never
    lets a bad classification travel downstream and get resolved leniently.
    """


def tag(value: str, level: Level) -> str:
    """Wrap *value* in an explicit classification span for :func:`gate_text`.

    Preconditions (checked here, not downstream): *level* must be a
    :class:`~private_mode.Level`; *value* must be a string that does not
    already contain a gate delimiter (nesting is rejected at the boundary
    rather than parsed -- a nested span is ambiguous about which level
    applies to what, so it is treated as an attack shape, not an input).
    """
    if not isinstance(level, Level):
        raise MalformedClassification(f"private_surface_gate.tag: level must be a Level, got {type(level).__name__}")
    if not isinstance(value, str):
        raise MalformedClassification("private_surface_gate.tag: value must be a string")
    if _TAG_PREFIX in value or _CLOSE_TAG in value:
        raise MalformedClassification("private_surface_gate.tag: value must not contain gate delimiters")
    return f"{_TAG_PREFIX}{level.name}{_TAG_SUFFIX}{value}{_CLOSE_TAG}"


def gate_text(text: str) -> str:
    """Resolve every classification span in *text*, deny-by-default.

    Post: the result never contains ``<<PRIVATE:`` and never contains the
    raw content of a span unless that span's level was exactly ``PUBLIC``.
    A span naming an unknown level, missing its closing delimiter, or
    containing a nested opening delimiter is malformed: it and everything
    after it in *text* is withheld rather than guessed at (fail closed --
    there is no reliable boundary to trust once a span is malformed).
    """
    if _TAG_PREFIX not in text:
        return text
    out: list[str] = []
    pos = 0
    while True:
        match = _OPEN_RE.search(text, pos)
        if match is None:
            out.append(text[pos:])
            break
        out.append(text[pos : match.start()])
        close = text.find(_CLOSE_TAG, match.end())
        nested = text.find(_TAG_PREFIX, match.end())
        level_name = match.group(1)
        malformed = close == -1 or level_name not in Level.__members__ or (nested != -1 and nested < close)
        if malformed:
            out.append(MALFORMED_PLACEHOLDER)
            break
        level = Level[level_name]
        inner = text[match.end() : close]
        out.append(inner if level is Level.PUBLIC else PLACEHOLDER)
        pos = close + len(_CLOSE_TAG)
    return "".join(out)
