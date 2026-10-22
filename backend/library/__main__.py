from __future__ import annotations

import argparse
import sys
from pathlib import Path

from library.db import library_source_dir
from library.ingest import ingest_directory


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m library")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest").add_argument("directory", nargs="?")
    args = p.parse_args()
    try:
        raw = args.directory or (str(library_source_dir()) if library_source_dir() else "")
        if not raw:
            raise ValueError("pass a directory or set LIBRARY_SOURCE_DIR")
        stats = ingest_directory(Path(raw))
        sys.stdout.write(
            f"indexed {stats['files_indexed']} file(s); skipped {stats['files_skipped']};"
            f" rejected {stats['files_rejected']}; removed {stats['files_removed']}\n"
        )
        return 0
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
