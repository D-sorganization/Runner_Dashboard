"""Parser for RM board-meeting ``consensus.md`` files (issue #1227, epic #1192).

The format is RM ``docs/templates/board-consensus.md``. The parser is
deliberately tolerant: that template's own tables carry separator rows with
the wrong column count (prettier splits ``|`` inside backticks), so table rows
are padded or truncated to the header width and any all-dash row is ignored.
Unfilled placeholders (``<item>``, ``YYYY-MM-DD``, ``...``) are dropped rather
than reported as real priorities.

Postcondition of :func:`parse_consensus`: active ranks are ``1..n`` in
document order and every returned item is non-empty.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from priorities.models import ActivePriority, BordaRow, Consensus, DeferredItem, MeetingMetadata

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_SUB_BULLET = re.compile(r"^\s*[-*]\s+([^:]+):\s*(.*)$")
_EM_DASH_SPLIT = re.compile(r"\s+[—–]\s+")
_PLAIN_DASH_SPLIT = re.compile(r"\s+-\s+")
_LABEL = re.compile(r"^\*\*(.+?):\*\*\s*$")
_PLACEHOLDERS = frozenset({"", "...", "…", "n", "yyyy-mm-dd"})

_METADATA_FIELDS = {
    "meeting date": "date",
    "quorum": "quorum",
    "instruction writer": "instruction_writer",
    "consensus reached": "consensus_reached",
}
_BULLET_FIELDS = {
    "assigned to": "assigned_to",
    "epic/issue": "tracking",
    "epic": "tracking",
    "issue": "tracking",
    "acceptance criteria": "acceptance",
}


def clean(cell: str) -> str:
    """Strip whitespace, backticks and bold markers wrapping a markdown cell."""
    return cell.strip().strip("`*").strip()


def is_placeholder(value: str) -> bool:
    """True for template filler: empty, ``<...>``, ``YYYY-MM-DD``, ``...``."""
    value = clean(value)
    return value.lower() in _PLACEHOLDERS or value.startswith("<")


def parse_table(lines: list[str]) -> list[dict[str, str]]:
    """Parse the first markdown table in ``lines`` into row dicts keyed by lower-case header.

    Tolerant by design: separator rows of any width are skipped and data rows
    are padded with ``""`` or truncated to the header width.
    """
    block: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            block.append([c.strip() for c in stripped.strip("|").split("|")])
        elif block:
            break
    if not block:
        return []
    header = [clean(c).lower() for c in block[0]]
    rows: list[dict[str, str]] = []
    for cells in block[1:]:
        if all(_SEPARATOR_CELL.match(c) for c in cells if c) and any(cells):
            continue
        padded = (cells + [""] * len(header))[: len(header)]
        rows.append({key: clean(value) for key, value in zip(header, padded, strict=True)})
    return rows


def _section(lines: list[str], match: Callable[[str], bool]) -> list[str]:
    """Lines under the first heading whose lower-cased title satisfies ``match``, up to the next peer heading."""
    level = 0
    body: list[str] = []
    for line in lines:
        heading = _HEADING.match(line)
        if level:
            if heading and len(heading.group(1)) <= level:
                break
            body.append(line)
        elif heading and match(clean(heading.group(2)).lower()):
            level = len(heading.group(1))
    return body


def _split_subsections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split a section into ``(heading title, body lines)`` pairs at its child headings."""
    parts: list[tuple[str, list[str]]] = []
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            parts.append((heading.group(2), []))
        elif parts:
            parts[-1][1].append(line)
    return parts


def _metadata(lines: list[str]) -> MeetingMetadata:
    values: dict[str, str] = {}
    for row in parse_table(_section(lines, lambda t: t.startswith("meeting metadata"))):
        key = _METADATA_FIELDS.get(row.get("field", "").lower())
        value = row.get("value", "")
        if key and not is_placeholder(value):
            values[key] = value
    return MeetingMetadata(**values)


def _head_fields(text: str) -> dict[str, str] | None:
    """``**item** — project — scope`` → fields; None when the item is a placeholder."""
    bold = _BOLD.search(text)
    item = clean(bold.group(1)) if bold else clean(text)
    if is_placeholder(item):
        return None
    rest = text[bold.end() :] if bold else ""
    rest = rest.strip().lstrip("—–-").strip()
    splitter = _EM_DASH_SPLIT if _EM_DASH_SPLIT.search(rest) else _PLAIN_DASH_SPLIT
    parts = [clean(p) for p in splitter.split(rest, maxsplit=1)] if rest else []
    fields = {"item": item, "project": parts[0] if parts else "", "scope": parts[1] if len(parts) > 1 else ""}
    return {k: ("" if k != "item" and is_placeholder(v) else v) for k, v in fields.items()}


def _active(lines: list[str]) -> list[ActivePriority]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _section(lines, lambda t: t.startswith("active priorities")):
        numbered = _NUMBERED.match(line)
        if numbered:
            current = _head_fields(numbered.group(1))
            if current is not None:
                entries.append(current)
            continue
        bullet = _SUB_BULLET.match(line)
        if bullet and current is not None:
            key = _BULLET_FIELDS.get(clean(bullet.group(1)).lower())
            if key and not is_placeholder(bullet.group(2)):
                current[key] = clean(bullet.group(2))
    return [ActivePriority(rank=i, **e) for i, e in enumerate(entries, start=1)]


def _deferred(lines: list[str]) -> list[DeferredItem]:
    rows = parse_table(_section(lines, lambda t: t.startswith("deferred backlog")))
    return [
        DeferredItem(
            item=row.get("item", ""),
            project=row.get("project", ""),
            reason=row.get("reason for deferral", ""),
            reassess="" if is_placeholder(row.get("reassess date", "")) else row.get("reassess date", ""),
        )
        for row in rows
        if not is_placeholder(row.get("item", ""))
    ]


def _int_or_none(value: str) -> int | None:
    try:
        return int(clean(value))
    except ValueError:
        return None


def _borda(lines: list[str]) -> list[BordaRow]:
    out: list[BordaRow] = []
    for row in parse_table(_section(lines, lambda t: t.startswith("borda count"))):
        rank = _int_or_none(row.get("rank", ""))
        if rank is None or rank < 1 or is_placeholder(row.get("item", "")):
            continue
        out.append(
            BordaRow(
                rank=rank,
                item=row["item"],
                project=row.get("project", ""),
                score=_int_or_none(row.get("total score", "")),
                votes=row.get("votes", ""),
            )
        )
    return out


def _labelled_paragraph(lines: list[str], label: str) -> str:
    """Text following a ``**Label:**`` line, up to the next label."""
    collecting = False
    parts: list[str] = []
    for line in lines:
        found = _LABEL.match(line.strip())
        if found:
            if collecting:
                break
            collecting = found.group(1).strip().lower() == label
            continue
        if collecting and line.strip():
            parts.append(clean(line))
    return " ".join(p for p in parts if not is_placeholder(p))


def _disagreements(lines: list[str]) -> list[str]:
    flags: list[str] = []
    section = _section(lines, lambda t: t.startswith("disagreement flags"))
    for title, body in _split_subsections(section):
        fields = {row.get("field", "").lower(): row.get("value", "") for row in parse_table(body)}
        seat = fields.get("flagging seat") or clean(title.split(":", 1)[-1].replace("(If Any)", ""))
        item = fields.get("consensus item disputed", "")
        if is_placeholder(seat) or is_placeholder(item):
            continue
        nature = fields.get("nature of disagreement", "")
        summary = f"{seat} disputes {item}" + ("" if is_placeholder(nature) else f" ({nature})")
        objection = _labelled_paragraph(body, "objection")
        flags.append(f"{summary}: {objection}" if objection else summary)
    return flags


def parse_consensus(text: str) -> Consensus:
    """Parse a consensus document. Never raises for malformed markdown; missing sections are empty."""
    lines = text.splitlines()
    result = Consensus(
        metadata=_metadata(lines),
        active=_active(lines),
        deferred=_deferred(lines),
        borda=_borda(lines),
        disagreements=_disagreements(lines),
    )
    assert [a.rank for a in result.active] == list(range(1, len(result.active) + 1)), "active ranks must be 1..n"
    assert all(a.item for a in result.active), "active items must be non-empty"
    return result
