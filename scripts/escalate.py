#!/usr/bin/env python3
"""Jump a PR to the front of the merge queue via the hub (slice 3, #2952)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Escalate a PR merge-queue position on the fleet hub")
    parser.add_argument("--repo", required=True, help="Repository (org/name or bare name)")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    url = os.environ.get("RUNNER_DASHBOARD_URL", "").rstrip("/")
    token = os.environ.get("RUNNER_DASHBOARD_TOKEN", "").strip()
    if not url or not token:
        print("RUNNER_DASHBOARD_URL and RUNNER_DASHBOARD_TOKEN are required", file=sys.stderr)
        return 1
    payload = json.dumps({"repo": args.repo, "pr": args.pr, "reason": args.reason}).encode()
    req = urllib.request.Request(  # noqa: S310
        f"{url}/api/staff/escalate",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        return 1
    print(json.dumps(body, indent=2))
    status = body.get("status")
    if status == "manual_required":
        print("\nRun on a box with merge-queue jump permission:\n", body.get("gh_personal_command", ""))
    elif status == "jump_ignored":
        print("\nJump was ignored by GitHub (check ruleset merge-queue bypass actors).", file=sys.stderr)
    return 0 if status == "enqueued" else 2


if __name__ == "__main__":
    sys.exit(main())
