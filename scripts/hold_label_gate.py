"""Fail closed when a pull request carries a merge-hold label.

Tools #6036 entered the merge queue and merged while ``do-not-merge`` was
still attached (RM #3091). Required checks never read that label. This module
is the pure decision used by ``ci-standard.yml`` and the tests around it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence

# Labels named by #3091. Matching is case-insensitive; the original text is
# what the error reports.
BLOCKING_HOLD_LABELS = frozenset({"do-not-merge", "needs-owner", "hold"})

_QUEUE_PR = re.compile(r"(?:^|/)pr-(\d+)(?=-|$)")
_MESSAGE_PR = re.compile(r"(?m)^\s*\*\s+(?:Pull request\s+)?#(\d+)\s*$")


class HoldLabelError(Exception):
    """A hold label is present, or the merge group did not name its PRs."""


def blocking_hold_labels(labels: Iterable[str]) -> tuple[str, ...]:
    """Return the hold labels from ``labels``, preserving order and case."""
    return tuple(label for label in labels if isinstance(label, str) and label.strip().lower() in BLOCKING_HOLD_LABELS)


def pull_requests_from_merge_group(head_ref: str, commit_message: str = "") -> tuple[int, ...]:
    """Pull request numbers named by a merge-queue ref or its commit message.

    Queue refs look like ``gh-readonly-queue/main/pr-6036-abc``. A grouped
    queue commit lists each entry as ``* #123`` or ``* Pull request #123``.
    """
    found: list[int] = []
    for pattern, text in ((_QUEUE_PR, head_ref), (_MESSAGE_PR, commit_message)):
        for match in pattern.finditer(text or ""):
            number = int(match.group(1))
            if number not in found:
                found.append(number)
    return tuple(found)


def assert_clear(labels: Iterable[str], *, context: str) -> None:
    """Raise ``HoldLabelError`` when ``labels`` contains a hold label."""
    blocking = blocking_hold_labels(labels)
    if blocking:
        names = ", ".join(blocking)
        raise HoldLabelError(f"{context} carries hold label(s): {names}")


def labels_from_json(payload: str) -> list[str]:
    """Parse a JSON array of label names. Any other shape fails closed."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HoldLabelError(f"hold-label payload is not JSON: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise HoldLabelError("hold-label payload must be a JSON array of strings")
    return data


def _gh_labels(number: int) -> str:
    """Live label names of PR ``number`` as JSON, read at call time via gh."""
    repo = os.environ.get("REPO", "")
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "labels",
            "--jq",
            "[.labels[].name]",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise HoldLabelError(f"could not read labels of PR #{number}; failing closed")
    return result.stdout


def check_event(
    event_name: str,
    *,
    labels_json: str,
    head_ref: str,
    message: str,
    fetch_labels: Callable[[int], str] = _gh_labels,
) -> None:
    """Raise ``HoldLabelError`` when the event's PR(s) carry a hold label.

    ``pull_request`` trusts the event payload. ``merge_group`` reads each queued
    PR's labels live, because the payload carries none. Calling this again late
    in the run narrows the window in which a hold added mid-run goes unseen.
    """
    if event_name == "pull_request":
        assert_clear(labels_from_json(labels_json), context="pull request")
        return
    numbers = pull_requests_from_merge_group(head_ref, message)
    if not numbers:
        raise HoldLabelError("merge_group did not name a pull request; failing closed")
    for number in numbers:
        assert_clear(labels_from_json(fetch_labels(number)), context=f"PR #{number}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-json", default="")
    parser.add_argument("--context", default="pull request")
    parser.add_argument("--extract-prs", action="store_true")
    parser.add_argument("--check-event", action="store_true")
    parser.add_argument("--head-ref", default="")
    parser.add_argument("--message", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.check_event:
        try:
            check_event(
                os.environ.get("EVENT_NAME", ""),
                labels_json=os.environ.get("PR_LABELS_JSON", ""),
                head_ref=os.environ.get("HEAD_REF", ""),
                message=os.environ.get("HEAD_MESSAGE", ""),
            )
        except HoldLabelError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        return 0
    if args.extract_prs == bool(args.labels_json):
        parser.error("choose exactly one of --labels-json or --extract-prs")
    try:
        if args.extract_prs:
            numbers = pull_requests_from_merge_group(args.head_ref, args.message)
            if not numbers:
                raise HoldLabelError("merge_group did not name a pull request; failing closed")
            for number in numbers:
                print(number)
            return 0
        assert_clear(labels_from_json(args.labels_json), context=args.context)
    except HoldLabelError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
