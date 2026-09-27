"""SPEC.md's Change Log must stay one renderable Markdown table (#1600).

PR #1554 replaced the separator row under the header with a new change-log
row, and a stray blank line split the rows in two, so GitHub rendered both
halves as plain text. The table sits inside a Prettier ignore fence because
Prettier splits cells on the ``|`` inside code spans and rewrites the rows.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "SPEC.md"

_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_ROW_RE = re.compile(r"^\| \d{4}-\d{2}-\d{2} \|")
FENCE_START = "<!-- prettier-ignore-start -->"
FENCE_END = "<!-- prettier-ignore-end -->"


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    return all(_SEPARATOR_CELL_RE.match(cell) for cell in _cells(line))


def _changelog_section() -> list[str]:
    lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    start = lines.index("## Change Log") + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("## "))
    return lines[start:end]


def _first_table(section: list[str]) -> list[str]:
    table: list[str] = []
    for line in section:
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    return table


def test_changelog_header_is_followed_by_separator_row() -> None:
    table = _first_table(_changelog_section())
    assert len(table) >= 3, "Change Log table needs a header, separator and rows"

    header, separator = table[0], table[1]
    assert _cells(header)[:3] == ["Date", "PR / Issue", "Summary"]
    assert _is_separator(separator), (
        f"line after the Change Log header must be a separator row, got: {separator[:80]!r}"
    )
    assert len(_cells(separator)) == len(_cells(header))
    assert sum(_is_separator(line) for line in table) == 1


def test_changelog_rows_form_one_unbroken_table() -> None:
    section = _changelog_section()
    table = _first_table(section)
    table_start = section.index(table[0])
    orphans = [line for line in section[table_start + len(table) :] if _ROW_RE.match(line)]
    assert not orphans, f"{len(orphans)} change-log rows sit outside the table, first: {orphans[0][:80]!r}"


def test_changelog_table_is_fenced_from_prettier() -> None:
    section = _changelog_section()
    table = _first_table(section)
    before = [line for line in section[: section.index(table[0])] if line.strip()]
    after = [line for line in section[section.index(table[-1]) + 1 :] if line.strip()]
    assert before and before[-1] == FENCE_START
    assert after and after[0] == FENCE_END
