# Vendored from D-sorganization/Repository_Management scripts/fork_pr_context_refs.py (RM#2338 @ 3436bd9d).
# Re-sync from upstream; do not fork. No local changes except ruff format at this repository's line length.
"""Recognize head-bearing Actions contexts without evaluating expressions.

Only Actions interpolations are scanned: github-script's JavaScript API client
is not the Actions ``github`` context. Opaque ancestor objects and unresolved
selection can carry head data; concrete base metadata cannot.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_INDEX = re.compile(r"[0-9]+")
# A collection element is distinct from an object key. Ancestors are inferred
# from these paths so adding a source selector also covers its opaque parents.
_ELEMENT = "[]"
_HEAD_ROOTS = frozenset(
    {
        ("head_ref",),
        ("event", "before"),
        ("event", "after"),
        ("event", "issue", "pull_request", "diff_url"),
        ("event", "issue", "pull_request", "patch_url"),
        ("event", "pull_request", "head"),
        ("event", "pull_request", "merge_commit_sha"),
        ("event", "pull_request", "diff_url"),
        ("event", "pull_request", "patch_url"),
        ("event", "workflow_run", "head_sha"),
        ("event", "workflow_run", "head_branch"),
        ("event", "workflow_run", "head_repository"),
        ("event", "workflow_run", "head_repository_id"),
        ("event", "workflow_run", "head_commit"),
        ("event", "workflow_run", "path"),
        ("event", "workflow_run", "referenced_workflows"),
        ("event", "workflow_run", "pull_requests", _ELEMENT, "head"),
    }
)


def _quoted_end(text: str, start: int) -> int:
    """Advance past a quoted string, honoring doubled quotes; otherwise to EOF."""
    quote = text[start]
    cursor = start + 1
    while cursor < len(text):
        if text[cursor] == quote:
            if cursor + 1 < len(text) and text[cursor + 1] == quote:
                cursor += 2
                continue
            return cursor + 1
        cursor += 1
    return cursor


def _expression_spans(text: str) -> Iterator[tuple[int, int, int]]:
    """Yield interpolation start, body end, and end; retain malformed EOF spans."""
    search = 0
    while (start := text.find("${{", search)) >= 0:
        cursor = start + 3
        while cursor < len(text):
            if text[cursor] in "'\"":
                cursor = _quoted_end(text, cursor)
            elif text.startswith("}}", cursor):
                yield start, cursor, cursor + 2
                search = cursor + 2
                break
            else:
                cursor += 1
        else:
            yield start, len(text), len(text)
            return


def outside_actions_expressions(text: str) -> str:
    """Return native-script text without Actions bodies, separated at each gap.

    Reuse the quote-aware span scanner so literal env-looking strings inside an
    Actions expression are not mistaken for native-language environment reads.
    """
    parts: list[str] = []
    end = 0
    for start, _, finish in _expression_spans(text):
        parts.append(text[end:start])
        end = finish
    parts.append(text[end:])
    return "\x00".join(parts)


def expression_string_literals(text: str) -> Iterator[str]:
    """Yield decoded Actions string literals, without evaluating operators.

    A literal can itself be executable script input. Native environment reads
    inside it retain their meaning, while a bare env.NAME string is metadata.
    """
    for start, body_end, _ in _expression_spans(text):
        cursor = start + 3
        while cursor < body_end:
            if text[cursor] in "'\"":
                quote = text[cursor]
                end = _quoted_end(text, cursor)
                if end <= body_end and text[end - 1] == quote:
                    yield text[cursor + 1 : end - 1].replace(quote * 2, quote)
                cursor = end
            else:
                cursor += 1


def render_literal_interpolations(text: str) -> str:
    """Substitute exact string-literal bodies; leave other expressions opaque.

    Preserve native neighbors so split member accesses remain visible. No
    operators, functions or dynamic values are evaluated or joined across gaps.
    Opaque gaps use a NUL analysis sentinel that cannot match native whitespace.
    """
    parts: list[str] = []
    end = 0
    for start, body_end, finish in _expression_spans(text):
        parts.append(text[end:start])
        body = text[start + 3 : body_end].strip()
        if (
            text[body_end:finish] == "}}"
            and len(body) >= 2
            and body[0] in "'\""
            and _quoted_end(body, 0) == len(body)
            and body[-1] == body[0]
        ):
            parts.append(body[1:-1].replace(body[0] * 2, body[0]))
        else:
            parts.append("\x00")
        end = finish
    parts.append(text[end:])
    return "".join(parts)


def _skip_space(text: str, cursor: int) -> int:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor


def _bracket_property(text: str, cursor: int) -> tuple[str | None, int, bool]:
    """Read a quoted object key or numeric collection index after an opening [."""
    cursor = _skip_space(text, cursor)
    if cursor < len(text) and text[cursor] in "'\"":
        quote = text[cursor]
        end = _quoted_end(text, cursor)
        if end <= cursor + 1 or text[end - 1] != quote:
            return None, end, True
        key = text[cursor + 1 : end - 1].replace(quote * 2, quote)
        if not _IDENTIFIER.fullmatch(key):
            return None, end, True
        cursor = end
    elif match := _INDEX.match(text, cursor):
        key, cursor = _ELEMENT, match.end()
    else:
        return None, cursor, True
    cursor = _skip_space(text, cursor)
    if cursor >= len(text) or text[cursor] != "]":
        return None, cursor, True
    return key.casefold(), cursor + 1, False


def _next_property(text: str, cursor: int) -> tuple[str | None, int, bool]:
    """Return a key/index/projection, cursor, and unresolved-selection flag."""
    cursor = _skip_space(text, cursor)
    if cursor >= len(text) or text[cursor] not in ".[":
        return None, cursor, False
    if text[cursor] == "[":
        return _bracket_property(text, cursor + 1)
    cursor = _skip_space(text, cursor + 1)
    if cursor < len(text) and text[cursor] == "*":
        return "*", cursor + 1, False
    match = _IDENTIFIER.match(text, cursor)
    if match:
        return match.group().casefold(), match.end(), False
    return None, cursor, True


def _reference_reads_head(text: str, cursor: int, roots: frozenset[tuple[str, ...]]) -> bool:
    """Walk possible code paths; a stopped or unknown ancestor remains opaque.

    Concrete metadata prunes all candidates. Projections match any edge, while
    numeric indexing matches collection elements, preserving PR number/base reads.
    """
    candidates = roots
    depth = 0
    while True:
        if any(len(root) == depth for root in candidates):
            return True
        key, cursor, unresolved = _next_property(text, cursor)
        if unresolved or key is None:
            return bool(candidates)
        candidates = frozenset(root for root in candidates if key == "*" or root[depth] == key)
        if not candidates:
            return False
        depth += 1


def _expression_reads_head(text: str, aliases: frozenset[tuple[str, ...]]) -> bool:
    contexts = {"github": _HEAD_ROOTS, "env": aliases}
    cursor = 0
    while cursor < len(text):
        if text[cursor] in "'\"":
            cursor = _quoted_end(text, cursor)
            continue
        match = _IDENTIFIER.match(text, cursor)
        if not match:
            cursor += 1
            continue
        previous = cursor - 1
        while previous >= 0 and text[previous].isspace():
            previous -= 1
        roots = contexts.get(match.group().casefold(), frozenset())
        if roots and (previous < 0 or text[previous] != ".") and _reference_reads_head(text, match.end(), roots):
            return True
        cursor = match.end()
    return False


def reads_head_context(text: str, aliases: Iterable[str] = ()) -> bool:
    """Return whether an interpolation reads head data or an opaque ancestor.

    Quotes and concrete properties are recognized, never evaluated. Malformed
    interpolation is scanned once through EOF rather than granting an exemption.
    Known head-bearing env aliases use the same path model, including opaque
    env objects and dynamic selection. Env keys are conservatively case-folded
    across platforms. This is lexical defense, not arbitrary-code data-flow proof.
    """
    roots = frozenset((name.casefold(),) for name in aliases)
    return any(
        _expression_reads_head(text[start + 3 : body_end], roots) for start, body_end, _ in _expression_spans(text)
    )
