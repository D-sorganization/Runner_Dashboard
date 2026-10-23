from __future__ import annotations

import re

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def extract_text_passages(raw: bytes, fmt: str) -> list[tuple[str | None, int, int, str]]:
    lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
    if not lines:
        return []
    passages: list[tuple[str | None, int, int, str]] = []
    section: str | None = None
    current: list[str] = []
    start: int = 1
    chars: int = 0
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        is_heading = fmt in ("md", "markdown") and stripped.startswith("#")
        if (is_heading or chars + len(line) > 1500) and current:
            text = "".join(current).strip()
            if text:
                passages.append((section, start, index - 1, text))
            current, chars, start = [], 0, index
        if is_heading:
            section = stripped.lstrip("#").strip() or None
        current.append(line)
        chars += len(line)
    if current and (text := "".join(current).strip()):
        passages.append((section, start, len(lines), text))
    return passages


def parse_filename(stem: str) -> tuple[str | None, str | None, int | None]:
    stem = stem.strip()
    if not stem:
        return None, None, None
    if " - " in stem:
        author, title = (part.strip() or None for part in stem.split(" - ", 1))
        return author, title, year(stem)
    return None, stem, year(stem)


def year(text: str) -> int | None:
    match = _YEAR_RE.search(text)
    if not match:
        return None
    value = int(match.group(0))
    return value if 1800 <= value <= 2100 else None
