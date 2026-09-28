"""Guard against pictographic emoji creeping back into UI source.

Part of the UX overhaul (epic #1718): the frontend uses a professional
line-icon style (SVG glyphs), not pictographic emoji. This test scans every
``*.ts``/``*.tsx`` file under ``frontend/src`` (excluding test files) and
fails if it finds a character in one of the pictographic/symbol emoji
ranges, listing every ``file:line`` hit.

A small set of typographic marks (check/cross/star/pencil marks) are not
pictographic and remain allowed.
"""

from __future__ import annotations

import pathlib

import pytest

FRONTEND_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"

# Typographic marks that are allowed even though some fall in adjacent
# emoji-ish blocks (U+2600-U+27BF dingbats/misc symbols range).
ALLOWED_CHARS = {
    "✓",  # ✓ CHECK MARK
    "✔",  # ✔ HEAVY CHECK MARK
    "✗",  # ✗ BALLOT X
    "✕",  # ✕ MULTIPLICATION X
    "★",  # ★ BLACK STAR
    "✎",  # ✎ LOWER RIGHT PENCIL
}

# Pictographic / symbol emoji ranges to flag.
EMOJI_RANGES = [
    (0x1F300, 0x1FAFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
    (0xFE0F, 0xFE0F),
]


def _is_flagged(ch: str) -> bool:
    if ch in ALLOWED_CHARS:
        return False
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in EMOJI_RANGES)


def _iter_source_files():
    for path in FRONTEND_SRC.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".ts", ".tsx"):
            continue
        posix = path.as_posix()
        if "__tests__" in posix or ".test." in posix:
            continue
        yield path


@pytest.mark.unit
def test_no_pictographic_emoji_in_frontend_src():
    hits: list[str] = []
    for path in sorted(_iter_source_files()):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for ch in line:
                if _is_flagged(ch):
                    rel = path.relative_to(FRONTEND_SRC.parents[1])
                    hits.append(f"{rel}:{lineno}: {ch!r} (U+{ord(ch):04X}) in: {line.strip()}")

    assert not hits, (
        "Pictographic emoji found in frontend/src (epic #1718 — use SVG line-icon glyphs instead):\n" + "\n".join(hits)
    )
