"""Queue-shepherd read-only GraphQL polling + recommendation report (#2929 slice 2).

Fetches the current merge queue and open auto-merge-armed PRs for one
repository via ``gh api graphql``, maps the response into the
:class:`~shared_scripts.queue_shepherd_logic.QueuedPR` snapshot shape slice 1
expects, and runs slice 1's pure checks over it. Strictly read-only: one
GraphQL request, no mutation — this never reorders the queue, cancels a run,
or arms/disarms a PR. That stays a human or a separate, explicitly-mutating
tool's job.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from shared_scripts.queue_shepherd_logic import (
    DependencyViolation,
    PathConflict,
    QueuedPR,
    find_dependency_order_violations,
    find_path_conflicts,
)

logger = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class QueueShepherdPollError(RuntimeError):
    """Raised when the GraphQL response cannot be read safely (fail closed)."""


#: Matches the issue's documented dependency convention: "Closes #N",
#: "Fixes #N", "Resolves #N", "Depends on #N" or "Refs #N" in a PR body.
#: Case-insensitive; reuses GitHub's own closing-keyword vocabulary plus the
#: two explicit dependency phrases slice 1's docstring calls out.
_DEPENDS_ON_RE = re.compile(r"(?:closes|fixes|resolves|depends on|refs)\s*#(\d+)", re.IGNORECASE)

QUEUE_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    mergeQueue {
      entries(first: 100) {
        totalCount
        pageInfo { hasNextPage }
        nodes {
          position
          pullRequest {
            number
            body
            files(first: 100) {
              totalCount
              pageInfo { hasNextPage }
              nodes { path }
            }
          }
        }
      }
    }
    armedPullRequests: pullRequests(states: OPEN, first: 100) {
      nodes {
        number
        autoMergeRequest { enabledAt }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class QueueShepherdReport:
    """Read-only summary: what the queue looks like, what slice 1 flagged.

    This is a recommendation signal only — "propose a reorder" — never an
    instruction this module (or anything downstream of it) executes.
    """

    repo: str
    queue_depth: int
    dependency_violations: tuple[DependencyViolation, ...] = ()
    path_conflicts: tuple[PathConflict, ...] = ()
    armed_not_queued: tuple[int, ...] = field(default_factory=tuple)


def _parse_depends_on(body: object) -> frozenset[int]:
    if not isinstance(body, str) or not body:
        return frozenset()
    return frozenset(int(match) for match in _DEPENDS_ON_RE.findall(body))


def _parse_files(files_block: object) -> frozenset[str]:
    if not isinstance(files_block, Mapping):
        return frozenset()
    if (files_block.get("pageInfo") or {}).get("hasNextPage"):
        raise QueueShepherdPollError(
            "pull request files() hasNextPage=true; truncated file list would hide path conflicts"
        )
    nodes = files_block.get("nodes") or []
    if not isinstance(nodes, list):
        raise QueueShepherdPollError("pull request files().nodes is not a list")
    paths: set[str] = set()
    for node in nodes:
        if isinstance(node, Mapping) and node.get("path"):
            paths.add(str(node["path"]))
    return frozenset(paths)


def queue_entries_from_graphql(payload: Mapping[str, Any]) -> list[QueuedPR]:
    """Parse ``repository.mergeQueue.entries`` into :class:`QueuedPR` snapshots.

    ``position`` comes straight from GitHub's ``MergeQueueEntry.position``
    when present; array order is the fallback for responses that omit it.
    Fails closed (raises) on anything that would silently hide part of the
    queue — a next page of entries or of a PR's changed files.
    """
    repo = payload.get("repository") or {}
    merge_queue = repo.get("mergeQueue") or {}
    entries_block = merge_queue.get("entries")
    if entries_block is None:
        return []
    if not isinstance(entries_block, Mapping):
        raise QueueShepherdPollError("mergeQueue.entries missing or not an object")
    if (entries_block.get("pageInfo") or {}).get("hasNextPage"):
        raise QueueShepherdPollError("mergeQueue.entries hasNextPage=true; truncated queue snapshot")

    nodes = entries_block.get("nodes") or []
    if not isinstance(nodes, list):
        raise QueueShepherdPollError("mergeQueue.entries.nodes is not a list")

    parsed: list[QueuedPR] = []
    for idx, node in enumerate(nodes, start=1):
        if not isinstance(node, Mapping):
            raise QueueShepherdPollError("mergeQueue entry node is not an object")
        pr = node.get("pullRequest")
        if not isinstance(pr, Mapping) or pr.get("number") is None:
            raise QueueShepherdPollError("mergeQueue entry missing pullRequest.number")
        position = node.get("position")
        if not isinstance(position, int):
            position = idx
        parsed.append(
            QueuedPR(
                number=int(pr["number"]),
                position=int(position),
                files=_parse_files(pr.get("files")),
                depends_on=_parse_depends_on(pr.get("body")),
            )
        )
    return parsed


def armed_unqueued_pr_numbers(payload: Mapping[str, Any], queued_numbers: Sequence[int] | set[int]) -> list[int]:
    """Open, auto-merge-armed PR numbers not already represented in the queue.

    Informational only: these PRs have no queue ``position`` yet, so they
    are reported for visibility but never fed into slice 1's checks.
    """
    repo = payload.get("repository") or {}
    prs_block = repo.get("armedPullRequests") or {}
    nodes = prs_block.get("nodes") or [] if isinstance(prs_block, Mapping) else []
    queued = set(queued_numbers)
    armed: set[int] = set()
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        number = node.get("number")
        if number is None or not node.get("autoMergeRequest"):
            continue
        if int(number) not in queued:
            armed.add(int(number))
    return sorted(armed)


def build_report(repo: str, entries: Sequence[QueuedPR], armed_not_queued: Sequence[int] = ()) -> QueueShepherdReport:
    """Run slice 1's pure checks over ``entries`` and assemble a report."""
    return QueueShepherdReport(
        repo=repo,
        queue_depth=len(entries),
        dependency_violations=tuple(find_dependency_order_violations(entries)),
        path_conflicts=tuple(find_path_conflicts(entries)),
        armed_not_queued=tuple(sorted(armed_not_queued)),
    )


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def fetch_queue_payload(owner: str, name: str, *, runner: CommandRunner = _default_runner) -> Mapping[str, Any]:
    """Issue exactly one read-only ``gh api graphql`` call and return ``data``.

    Never calls a mutation: this is the only network boundary in the module,
    and ``QUEUE_QUERY`` has no ``mutation`` in it.
    """
    proc = runner(["gh", "api", "graphql", "-f", f"query={QUEUE_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"])
    if proc.returncode != 0:
        raise QueueShepherdPollError(proc.stderr.strip() or proc.stdout.strip() or "gh api graphql failed")
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise QueueShepherdPollError(f"gh api graphql returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise QueueShepherdPollError("gh api graphql response is not an object")
    if parsed.get("errors"):
        raise QueueShepherdPollError(f"GraphQL errors: {parsed['errors']}")
    data = parsed.get("data")
    if not isinstance(data, Mapping):
        raise QueueShepherdPollError("gh api graphql response missing data")
    return data


def poll_queue(owner: str, name: str, *, runner: CommandRunner = _default_runner) -> QueueShepherdReport:
    """Fetch, map and report — the one entry point callers should use.

    Read-only end to end. Never reorders the queue, cancels a run, or
    arms/disarms a PR.
    """
    payload = fetch_queue_payload(owner, name, runner=runner)
    entries = queue_entries_from_graphql(payload)
    armed_not_queued = armed_unqueued_pr_numbers(payload, {entry.number for entry in entries})
    return build_report(f"{owner}/{name}", entries, armed_not_queued)


def _violation_line(v: DependencyViolation) -> str:
    return (
        f"  dependency-order: #{v.pr} (pos {v.pr_position}) blocked by #{v.blocked_by} "
        f"(pos {v.blocked_by_position}) — recommend moving #{v.blocked_by} ahead of #{v.pr}"
    )


def _conflict_line(c: PathConflict) -> str:
    paths = ", ".join(sorted(c.overlapping_paths))
    return (
        f"  path-conflict: #{c.behind_pr} (pos {c.behind_position}) overlaps "
        f"#{c.ahead_pr} (pos {c.ahead_position}) on {paths}"
    )


def render_brief(report: QueueShepherdReport) -> str:
    """Render ``report`` as a few lines of plain text for a status line."""
    lines = [f"queue-shepherd {report.repo}: depth={report.queue_depth}"]
    lines += [_violation_line(v) for v in report.dependency_violations] or ["  dependency-order: clean"]
    lines += [_conflict_line(c) for c in report.path_conflicts] or ["  path-conflict: none"]
    if report.armed_not_queued:
        armed = ", ".join(f"#{number}" for number in report.armed_not_queued)
        lines.append(f"  armed, not yet queued: {armed}")
    return "\n".join(lines)
