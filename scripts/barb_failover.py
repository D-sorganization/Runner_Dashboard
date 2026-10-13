#!/usr/bin/env python3
"""Manual Barb authority failover helper (#1800, BR-06; ADR 0008).

Runbook: ``docs/operations/barb-authority-failover.md``. Failover is manual by design (owner ruling,
RM#1853): the operator bumps the authority epoch, and the persisted epoch fence refuses the old authority.

Every subcommand is offline and headless: it works on a local conversation database file, never on the
network, and prints exactly one JSON document on stdout.

  status  --db PATH                              show the persisted fence and migration state
  backup  --db PATH                              write a self-contained online backup next to the file
  advance --db PATH --epoch N --node NAME        raise the fence (promote the new authority's restored
          [--reason TEXT]                        copy, or fence the old authority before it rejoins)
  env     --node NAME --url URL --epoch N        the settings every node needs after the failover

Exit codes: 0 done, 1 refused (the fence would move backwards or change node without an epoch bump),
2 usage error (missing database, bad URL or epoch).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from staff.barb_fence import FencedAuthorityError  # noqa: E402
from staff.conversation_migrations import backup_database  # noqa: E402
from staff.conversations import ConversationStore  # noqa: E402

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2


class UsageError(ValueError):
    """Bad input: exit 2."""


def _existing_db(raw: str) -> Path:
    """Pre: ``raw`` names an existing, non-empty file (the script never creates a database)."""
    path = Path(raw)
    if not path.is_file() or path.stat().st_size == 0:
        raise UsageError(f"no conversation database at {path}")
    return path


def _epoch(raw: int) -> int:
    if raw < 1:
        raise UsageError(f"--epoch must be >= 1, got {raw}")
    return raw


def _url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UsageError(f"--url must be http(s)://host[:port], got {raw!r}")
    return url


def _open(raw: str) -> ConversationStore:
    return ConversationStore(_existing_db(raw))


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    store = _open(args.db)
    try:
        fence = store.barb_epoch_fence()
        return {"db": str(store.path), "fence": fence.to_dict() if fence else None}
    finally:
        store.close()


def cmd_backup(args: argparse.Namespace) -> dict[str, Any]:
    backup = backup_database(_existing_db(args.db))
    if backup is None:
        raise UsageError(f"backup of {args.db} failed (see the log)")
    return {"db": args.db, "backup": str(backup)}


def cmd_advance(args: argparse.Namespace) -> dict[str, Any]:
    epoch = _epoch(args.epoch)
    if not args.node.strip():
        raise UsageError("--node must not be empty")
    store = _open(args.db)
    try:
        fence = store.advance_barb_epoch_fence(epoch, args.node.strip(), args.reason)
        return {"db": str(store.path), "fence": fence.to_dict()}
    finally:
        store.close()


def cmd_env(args: argparse.Namespace) -> dict[str, Any]:
    url, epoch = _url(args.url), _epoch(args.epoch)
    if not args.node.strip():
        raise UsageError("--node must not be empty")
    return {
        "env": {
            "BARB_AUTHORITY_NODE": args.node.strip(),
            "BARB_AUTHORITY_URL": url,
            "BARB_AUTHORITY_EPOCH": str(epoch),
            "FLEET_API_URL": url,
        }
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("status", cmd_status), ("backup", cmd_backup)):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        p.set_defaults(func=func)
    p = sub.add_parser("advance")
    p.add_argument("--db", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_advance)
    p = sub.add_parser("env")
    p.add_argument("--node", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.set_defaults(func=cmd_env)
    return parser


def _emit(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Post: exactly one JSON document on stdout; returns 0, 1 (refused) or 2 (usage error)."""
    args = _parser().parse_args(argv)
    try:
        _emit(args.func(args))
    except FencedAuthorityError as exc:
        _emit({"error": str(exc)})
        return EXIT_REFUSED
    except UsageError as exc:
        _emit({"error": str(exc)})
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
