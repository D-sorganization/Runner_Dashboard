#!/usr/bin/env python3
"""One-shot importer: DO_NOT_DISPATCH.txt -> Staff Hub dispatch exclusions.

Input lines are ``<Repo> <issue>  # owner: reason`` (``#`` lines and blanks are
skipped; ``<issue>`` may be ``*`` for the whole repo). The text after ``#`` is the
reason; the part before its first ``:`` (when present) is the owner, else
``DEFAULT_OWNER``. Dry-run by default: prints the POST bodies and sends nothing.
``--apply`` POSTs each to ``$RUNNER_DASHBOARD_URL/api/staff/exclusions`` with the
bearer token in ``$RUNNER_DASHBOARD_TOKEN``. Re-running is idempotent (same
repo/issue replaces the entry).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("import_dispatch_exclusions")

DEFAULT_OWNER = "do-not-dispatch-import"


def parse_line(line: str) -> dict[str, Any] | None:
    """Parse one line into a POST body, or None for blank/comment lines. Raises ValueError if malformed."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    head, _, comment = stripped.partition("#")
    parts = head.split()
    if len(parts) != 2:
        raise ValueError(f"expected '<Repo> <issue> # owner: reason', got {line.rstrip()!r}")
    repo, issue_raw = parts
    if issue_raw == "*":
        issue: int | None = None
    elif issue_raw.isdigit() and int(issue_raw) >= 1:
        issue = int(issue_raw)
    else:
        raise ValueError(f"issue must be a positive integer or '*', got {issue_raw!r}")
    reason = comment.strip()
    if not reason:
        raise ValueError(f"missing '# owner/reason' comment: {line.rstrip()!r}")
    owner_part, sep, _rest = reason.partition(":")
    owner = owner_part.strip() if sep and owner_part.strip() else DEFAULT_OWNER
    return {"repo": repo, "issue": issue, "owner": owner, "reason": reason}


def parse_text(text: str) -> list[dict[str, Any]]:
    return [body for line in text.splitlines() if (body := parse_line(line)) is not None]


def _post(url: str, token: str, body: dict[str, Any]) -> int:
    req = urllib.request.Request(  # noqa: S310 - operator-supplied hub URL
        f"{url.rstrip('/')}/api/staff/exclusions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return int(resp.status)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="DO_NOT_DISPATCH.txt")
    ap.add_argument("--apply", action="store_true", help="actually POST (default: dry run)")
    ap.add_argument("--url", default=os.environ.get("RUNNER_DASHBOARD_URL", ""))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        bodies = parse_text(args.path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("cannot parse %s: %s", args.path, exc)
        return 2
    if not args.apply:
        for body in bodies:
            log.info("DRY-RUN POST /api/staff/exclusions %s", json.dumps(body))
        log.info("%d exclusion(s) would be created; pass --apply to send", len(bodies))
        return 0
    token = os.environ.get("RUNNER_DASHBOARD_TOKEN", "")
    if not args.url or not token:
        log.error("--apply needs --url/$RUNNER_DASHBOARD_URL and $RUNNER_DASHBOARD_TOKEN")
        return 2
    failed = 0
    for body in bodies:
        try:
            log.info("POST %s#%s -> %s", body["repo"], body["issue"], _post(args.url, token, body))
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            log.error("POST %s#%s failed: %s", body["repo"], body["issue"], exc)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
