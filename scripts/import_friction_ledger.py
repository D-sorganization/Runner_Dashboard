#!/usr/bin/env python3
"""Import the shell orchestrator's FRICTION_LEDGER.md table into the dashboard friction ledger.

Dry-run by default: parses the markdown table and prints the rows that WOULD be created.
``--apply`` POSTs them to ``<url>/api/v1/fleet/friction`` using the bearer token in
``$FLEET_TOKEN`` (needs ``coordination.write``). Never run ``--apply`` twice: rows are not de-duplicated.

Usage: python scripts/import_friction_ledger.py FRICTION_LEDGER.md [--apply] [--url http://127.0.0.1:8321]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

_ROW = re.compile(r"^\|\s*F(\d+)\s*\|")
_REF = re.compile(r"(?:https?://\S+|[A-Za-z][A-Za-z_-]*\s?#\d+|(?<![\w/])#\d+)")
_STATUS_ORDER = (("CLOSED", "closed"), ("WAITING", "waiting"), ("IN PROGRESS", "in_progress"), ("OPEN", "open"))


def _status(text: str) -> str:
    head = text.strip().upper()
    for prefix, value in _STATUS_ORDER:
        if head.startswith(prefix):
            return value
    return "open"


def _title(seen: str) -> str:
    first = re.split(r"(?<=[.;:])\s|\s\(", seen, maxsplit=1)[0].strip()
    return (first[:117] + "...") if len(first) > 120 else first


def parse_ledger(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (rows, warnings). A row is the create payload for POST /api/v1/fleet/friction."""
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        label = f"F{match.group(1)}"
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            warnings.append(f"{label}: expected 5 columns, found {len(cells)}; skipped")
            continue
        _, seen, fix, detector, status_text = cells
        status = _status(status_text)
        refs = _REF.findall(fix)
        fix_ref = ", ".join(dict.fromkeys(r.strip() for r in refs)) if refs else ""
        if status == "closed" and not fix_ref:
            fix_ref = f"FRICTION_LEDGER.md#{label} (documented fix, no PR)"
            warnings.append(f"{label}: closed without a PR/commit ref; imported with a ledger pointer")
        if status == "closed" and detector.strip().lower() in {"", "n/a", "none"}:
            warnings.append(f"{label}: closed with no real detector ({detector!r}); review after import")
        rows.append(
            {
                "ledger_id": label,
                "title": _title(seen),
                "seen": seen,
                "fix": fix,
                "fix_ref": fix_ref,
                "detector": detector,
                "status": status,
                "ledger_status_text": status_text,
            }
        )
    return rows, warnings


def _post(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {k: payload[k] for k in ("title", "seen", "fix", "fix_ref", "detector", "status")}
    req = urllib.request.Request(  # noqa: S310 - operator-supplied hub URL
        f"{url.rstrip('/')}/api/v1/fleet/friction",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--apply", action="store_true", help="POST rows to the hub (default: dry-run)")
    parser.add_argument("--url", default=os.environ.get("FLEET_URL", "http://127.0.0.1:8321"))
    args = parser.parse_args(argv)
    rows, warnings = parse_ledger(args.ledger.read_text(encoding="utf-8"))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not args.apply:
        print(json.dumps({"dry_run": True, "count": len(rows), "rows": rows}, indent=2))
        return 0
    token = os.environ.get("FLEET_TOKEN", "")
    if not token:
        print("error: set FLEET_TOKEN to apply", file=sys.stderr)
        return 2
    for row in rows:
        created = _post(args.url, token, row)
        print(f"{row['ledger_id']} -> {created['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
