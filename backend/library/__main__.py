from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from library.db import library_db_path, library_source_dir
from library.ingest import ingest_directory
from library.sources import add_source, get_source, list_sources, remove_source, update_source
from library.vector_index import backfill_db
from library.verify import verify_content


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m library")
    sub = p.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("directory", nargs="?")
    ingest.add_argument("--source-id", help="Use this registered source and its full root path")
    ingest.add_argument("--max-files", type=int, help="Bound this scan; incomplete scans never reconcile removals")
    verify = sub.add_parser("verify", help="Verify citations and repository-relative Markdown links")
    verify.add_argument("--file", required=True)
    verify.add_argument("--run-id")
    verify.add_argument("--library-db", type=Path, help="Explicit read-only Alexandria database")
    verify.add_argument("--knowledge-dir", type=Path, help="Explicit local knowledge-pack directory")
    verify.add_argument("--local-only", action="store_true", help="Never contact configured fleet peers")
    verify.add_argument("--repo-root", type=Path)
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
        if args.command == "verify":
            db = args.library_db or library_db_path()
            if args.library_db is not None and not db.is_file():
                raise ValueError("explicit library database does not exist")
            if args.knowledge_dir is not None and not args.knowledge_dir.is_dir():
                raise ValueError("explicit knowledge directory does not exist")
            explicit_inputs = args.library_db is not None or args.knowledge_dir is not None
            document_path = None
            if args.repo_root is not None:
                candidate = Path(args.file).absolute()
                root = args.repo_root.resolve()
                if root in candidate.parents:
                    document_path = candidate
            result = verify_content(
                db,
                Path(args.file).read_text(encoding="utf-8"),
                run_id=args.run_id,
                repo_root=args.repo_root,
                document_path=document_path,
                knowledge_dir=args.knowledge_dir,
                allow_ambient_knowledge=not explicit_inputs,
                local_only=args.local_only,
            )
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            return 0 if result["verified"] else 1
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
        db = library_db_path()
        if args.max_files is not None and not args.source_id:
            raise ValueError("--max-files requires --source-id")
        if args.source_id and args.directory:
            raise ValueError("--source-id uses the registered source root; omit directory")
        if args.source_id:
            raw = str(get_source(db, args.source_id)["root_path"])
            source_id = args.source_id
        else:
            raw = args.directory or (str(library_source_dir()) if library_source_dir() else "")
            source_id = "default"
        if not raw:
            raise ValueError("pass a directory or set LIBRARY_SOURCE_DIR")
        stats = ingest_directory(Path(raw), db_path=db, source_id=source_id, max_files=args.max_files)
        sys.stdout.write(
            f"indexed {stats['files_indexed']} file(s); skipped {stats['files_skipped']};"
            f" rejected {stats['files_rejected']}; removed {stats['files_removed']};"
            f" scan_complete={str(stats['scan_complete']).lower()}\n"
        )
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
