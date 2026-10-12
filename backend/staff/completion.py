"""Typed completion evidence for staff runs (#1808).

A succeeded run only advances its work item as far as its recorded ``artifact`` allows.
Free-text summaries never decide state: prose such as "PR #1" cannot move a work item.

Mapping for a succeeded run: ``pr_open`` -> ``waiting_on_ci``; ``pr_merged`` / ``deployed`` ->
``done``; no (or unknown) artifact -> ``needs_evidence``.
"""

from __future__ import annotations

ARTIFACT_PR_OPEN = "pr_open"
ARTIFACT_PR_MERGED = "pr_merged"
ARTIFACT_DEPLOYED = "deployed"
ARTIFACT_KINDS: tuple[str, ...] = (ARTIFACT_PR_OPEN, ARTIFACT_PR_MERGED, ARTIFACT_DEPLOYED)

_STATE_BY_ARTIFACT = {
    ARTIFACT_PR_OPEN: "waiting_on_ci",
    ARTIFACT_PR_MERGED: "done",
    ARTIFACT_DEPLOYED: "done",
}
NEEDS_EVIDENCE = "needs_evidence"


def work_item_state_for_success(artifact: str) -> str:
    """The work item state a succeeded run with ``artifact`` earns.

    Post: never ``done`` unless ``artifact`` is a merged PR or a deployment; an unknown value is
    treated as no evidence.
    """
    return _STATE_BY_ARTIFACT.get(artifact or "", NEEDS_EVIDENCE)
