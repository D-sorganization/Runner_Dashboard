"""Deterministic source enumeration shared by bounded ingest passes."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from library.sources import matches_source_globs

_INGEST_SUFFIXES = {".pdf", ".epub", ".md", ".markdown", ".txt"}


def eligible_paths(source_root: Path, include_globs: list[str], exclude_globs: list[str]) -> Iterator[Path]:
    """Yield supported, included source files in stable cursor order."""
    for current, directories, filenames in os.walk(source_root, onerror=_raise_walk_error):
        directories.sort()
        for filename in sorted(filenames):
            path = Path(current) / filename
            if (
                path.is_file()
                and path.suffix.lower() in _INGEST_SUFFIXES
                and matches_source_globs(path.relative_to(source_root).as_posix(), include_globs, exclude_globs)
            ):
                yield path


def _raise_walk_error(error: OSError) -> None:
    raise error
