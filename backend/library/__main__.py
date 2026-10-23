from __future__ import annotations

import argparse
import sys
from pathlib import Path

from library.db import library_db_path, library_source_dir
from library.ingest import ingest_directory
from library.sources import add_source, list_sources, remove_source, update_source
from library.vector_index import backfill_db


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m library")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest").add_argument("directory", nargs="?")
    embed_p = sub.add_parser("embed", help="Backfill passage embeddings (sqlite-vec)")
    embed_p.add_argument("--batch-size", type=int, default=32)
    sources = sub.add_parser("sources")
    source_cmd = sources.add_subparsers(dest="source_command", required=True)
    source_cmd.add_parser("list")
    add = source_cmd.add_parser("add")
    add.add_argument("id")
    add.add_argument("label")
    add.add_argument("root_path")
    add.add_argument("--shared", action="store_true")
    for name in ("share", "unshare"):
        cmd = source_cmd.add_parser(name)
        cmd.add_argument("id")
    remove = source_cmd.add_parser("remove")
    remove.add_argument("id")
    args = p.parse_args()
    try:
        if args.command == "embed":
            count = backfill_db(library_db_path(), batch_size=args.batch_size)
            sys.stdout.write(f"embedded {count} passage(s)\n")
            return 0
        if args.command == "sources":
            db = library_db_path()
            if args.source_command == "list":
                rows = list_sources(db)
                for row in rows:
                    sys.stdout.write(
                        f"{row['id']}\t{row['label']}\t{row['root_path']}\tshared={str(row['shared']).lower()}\t"
                        f"availability={row['availability']}\n"
                    )
            elif args.source_command == "add":
                add_source(db, source_id=args.id, label=args.label, root_path=args.root_path, shared=args.shared)
            elif args.source_command in {"share", "unshare"}:
                update_source(db, args.id, shared=args.source_command == "share")
            else:
                remove_source(db, args.id)
            return 0
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
