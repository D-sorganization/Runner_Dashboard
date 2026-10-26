#!/usr/bin/env python3
"""GitHub Actions helper: enforce trusted actors for priority:expedite."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from escalation.constants import EXPEDITE_LABEL
from escalation.policy import is_trusted_github_actor


def main() -> int:
    sender = os.environ.get("GITHUB_ACTOR", "")
    repo = os.environ["GITHUB_REPOSITORY"]
    pr = os.environ["PR_NUMBER"]
    owner, name = repo.split("/", 1)
    trusted = is_trusted_github_actor(sender)
    outcome = "applied" if trusted else "stripped"
    reason = (
        "priority:expedite applied by trusted actor"
        if trusted
        else f"priority:expedite removed: {sender} is not authorized to escalate"
    )
    if not trusted:
        subprocess.run(
            ["gh", "pr", "edit", pr, "--repo", repo, "--remove-label", EXPEDITE_LABEL],
            check=True,
        )
        body = (
            "### Escalation label removed\n\n"
            f"Only trusted fleet operators may apply `{EXPEDITE_LABEL}`.\n\n"
            f"- Actor: `{sender}`"
        )
        subprocess.run(
            ["gh", "pr", "comment", pr, "--repo", repo, "--body", body],
            check=True,
        )
    hub_url = os.environ.get("RUNNER_DASHBOARD_URL", "").rstrip("/")
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
    if hub_url and hub_token:
        payload = json.dumps(
            {
                "repo": repo,
                "pr_number": int(pr),
                "github_actor": sender,
                "outcome": outcome,
                "reason": reason,
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310
            f"{hub_url}/api/fleet/escalation-events",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {hub_token}",
                "X-Requested-With": "XMLHttpRequest",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30):  # noqa: S310
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
