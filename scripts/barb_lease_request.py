#!/usr/bin/env python3
"""Build/apply Barb lease rd-threads requests (#3301). ``apply`` is dry-run unless ``BARB_LEASE_REQUESTS=on``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from staff.barb_continuity import get_barb_continuity_store  # noqa: E402
from staff.barb_lease_requests import apply_request, build_request, remote_holders_from_env  # noqa: E402

log = logging.getLogger("barb_lease_request")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("action", choices=sorted(("status", "checkout", "heartbeat", "release")))
    b.add_argument("scope")
    b.add_argument("--sender", required=True)
    for name in ("holder", "summary", "persona", "machine", "turnover-json"):
        b.add_argument(f"--{name.replace('_', '-')}", default="")
    b.add_argument("--epoch", type=int, default=None)
    b.add_argument("--ttl-seconds", type=int, default=None)
    b.add_argument("--force", action="store_true")
    a = sub.add_parser("apply")
    a.add_argument("--json", required=True)
    a.add_argument("--store", default="")
    args = parser.parse_args(argv)

    if args.cmd == "build":
        kw: dict[str, Any] = {
            k: v
            for k, v in {
                "holder": args.holder or None,
                "epoch": args.epoch,
                "summary": args.summary,
                "persona": args.persona,
                "machine": args.machine,
                "force": args.force,
                "turnover": json.loads(args.turnover_json) if args.turnover_json.strip() else None,
                "ttl_seconds": args.ttl_seconds,
            }.items()
            if v is not None and v != ""
        }
        env = build_request(args.action, args.scope, sender=args.sender, now=time.time(), **kw)
        json.dump(env, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    enabled = os.environ.get("BARB_LEASE_REQUESTS", "").strip().lower() == "on"
    db = Path(args.store) if args.store.strip() else None
    store = get_barb_continuity_store(db) if db else get_barb_continuity_store()
    try:
        raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text(encoding="utf-8")
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            raise ValueError("envelope must be a JSON object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.error("could not read envelope: %s", exc)
        return 2

    result, answer = apply_request(
        store, msg, remote_holders_from_env(), now=datetime.now(UTC), dry_run=not enabled
    )
    json.dump({"ok": result.get("ok"), "result": result, "answer": answer, "applied": enabled}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
