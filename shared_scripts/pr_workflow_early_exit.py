"""Early-exit guard for pull_request CI workflows (issue #2764)."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardDecision:
    skip: bool
    reason: str = ""


def evaluate_pull_request_guard(
    *, event_name: str, pr_state: str, event_head_sha: str, live_head_sha: str
) -> GuardDecision:
    """Return whether heavy jobs should no-op for this workflow run."""
    if event_name != "pull_request":
        return GuardDecision(skip=False)
    state = (pr_state or "").lower()
    if state and state != "open":
        return GuardDecision(skip=True, reason=f"pull request is {state}")
    if event_head_sha and live_head_sha and event_head_sha != live_head_sha:
        return GuardDecision(skip=True, reason="event pull_request.head.sha is no longer the PR head")
    return GuardDecision(skip=False)


def _github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        raise RuntimeError("GITHUB_OUTPUT is not set")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate PR workflow early-exit guard")
    parser.add_argument("--event-name", default=os.environ.get("EVENT_NAME", ""))
    parser.add_argument("--pr-state", default=os.environ.get("PR_STATE", ""))
    parser.add_argument("--event-head-sha", default=os.environ.get("EVENT_HEAD_SHA", ""))
    parser.add_argument("--live-head-sha", default="")
    parser.add_argument("--github-api", action="store_true", help="Fetch live head sha via gh api")
    args = parser.parse_args(argv)

    live_head = args.live_head_sha
    if args.github_api:
        import json
        import urllib.error
        import urllib.request

        repo = os.environ.get("GITHUB_REPOSITORY", "")
        pr_number = os.environ.get("PR_NUMBER", "")
        token = os.environ.get("GH_TOKEN", "")
        if not repo or not pr_number or not token:
            print("missing GITHUB_REPOSITORY, PR_NUMBER, or GH_TOKEN", file=sys.stderr)
            return 2
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            print(exc.read().decode(), file=sys.stderr)
            return exc.code if exc.code else 1
        live_head = str(payload.get("head", {}).get("sha") or "")
        live_state = str(payload.get("state") or "")
        args.pr_state = live_state
        args.live_head_sha = live_head

    decision = evaluate_pull_request_guard(
        event_name=args.event_name,
        pr_state=args.pr_state,
        event_head_sha=args.event_head_sha,
        live_head_sha=live_head,
    )
    _github_output("skip", "true" if decision.skip else "false")
    _github_output("reason", decision.reason.replace("\n", " "))
    if decision.skip:
        print(f"::notice::PR early-exit: {decision.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
