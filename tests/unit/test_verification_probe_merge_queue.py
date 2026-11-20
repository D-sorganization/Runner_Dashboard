"""``GhCliPrProbe`` reads the newest merge-queue event, not the first page of the timeline.

GitHub's issue timeline is oldest-first. Reading only ``per_page=30`` meant a PR with a
long history reported the queue state of its first 30 events: queued late reads as not
queued, and an early add followed by a later removal reads as still queued.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from staff.verification_probe import GhCliPrProbe
from staff.verification_types import GitHubLookupError

pytestmark = pytest.mark.unit

PULL = {"number": 9, "state": "open", "merged_at": None, "head": {"sha": "abc"}, "auto_merge": None}
CHECKS = {"check_runs": [{"status": "completed", "conclusion": "success"}]}


def _probe(queue_events: list[str] | int, calls: list[list[str]]) -> GhCliPrProbe:
    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        path = next(a for a in argv[2:] if a.startswith("/"))
        if "/timeline" in path:
            if isinstance(queue_events, int):
                return subprocess.CompletedProcess(argv, queue_events, "", "HTTP 502")
            return subprocess.CompletedProcess(argv, 0, "".join(f"{e}\n" for e in queue_events), "")
        body = PULL if path.endswith("/pulls/9") else CHECKS
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    return GhCliPrProbe(org="o", run=run)


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([], False),
        (["added_to_merge_queue"], True),
        (["added_to_merge_queue", "removed_from_merge_queue"], False),
        (["added_to_merge_queue", "removed_from_merge_queue", "added_to_merge_queue"], True),
    ],
)
def test_latest_queue_event_decides(events: list[str], expected: bool) -> None:
    calls: list[list[str]] = []
    assert _probe(events, calls).get("r", 9).in_merge_queue is expected


def test_timeline_is_read_across_every_page_filtered_to_queue_events() -> None:
    calls: list[list[str]] = []
    _probe([], calls).get("r", 9)
    (timeline_call,) = [c for c in calls if any("/timeline" in a for a in c)]
    assert "--paginate" in timeline_call
    assert "/repos/o/r/issues/9/timeline?per_page=100" in timeline_call
    jq = timeline_call[timeline_call.index("--jq") + 1]
    assert "added_to_merge_queue" in jq and "removed_from_merge_queue" in jq


def test_armed_pr_skips_the_timeline() -> None:
    calls: list[list[str]] = []
    probe = _probe(["added_to_merge_queue"], calls)
    pull_armed = {**PULL, "auto_merge": {"merge_method": "squash"}}

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if argv[-1].endswith("/pulls/9"):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(pull_armed), "")
        return probe._run(argv, **kw)

    pr = GhCliPrProbe(org="o", run=run).get("r", 9)
    assert (pr.auto_merge_armed, pr.in_merge_queue) == (True, False)
    assert not any("/timeline" in a for c in calls for a in c)


def test_timeline_failure_raises_lookup_error() -> None:
    with pytest.raises(GitHubLookupError):
        _probe(1, []).get("r", 9)
