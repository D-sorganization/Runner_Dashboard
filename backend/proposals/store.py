"""GitHub Issue store for Board Proposals (issue #1284, CR-7).

Interacts with D-sorganization/Repository_Management using gh_utils.
Issues are labelled 'board:proposal' and 'needs-decision', and are formatted
to mirror the real board-proposal issue form
(``Repository_Management/.github/ISSUE_TEMPLATE/board-proposal.yml``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from gh_utils import gh_api, gh_api_write
from proposals.models import CreateProposalRequest

log = logging.getLogger("dashboard.proposals.store")

RM_REPO = "D-sorganization/Repository_Management"
PROPOSAL_LABEL = "board:proposal"
NEEDS_DECISION_LABEL = "needs-decision"

_SECTION_RE = re.compile(r"^###\s+(.*?)\s*$", re.MULTILINE)
_NO_RESPONSE = "_no response_"
# Only these labels count as a Board decision outcome. `board:needs-info` is
# secretary bookkeeping, not a decision, and there is no `board:decided`
# label in the taxonomy (a closed issue with no outcome label counts as
# "decided" through `extract_decision_info`'s state fallback instead).
_KNOWN_DECISIONS: dict[str, str] = {
    "board:accepted": "accepted",
    "board:declined": "declined",
    "board:deferred": "deferred",
}


class ProposalStoreError(RuntimeError):
    """Raised when proposal issue data could not be read from GitHub.

    Callers (``proposals.service``) must not treat this as "no proposals" —
    see review #1444 defect 3: a failed read must surface as an error, never
    silently degrade to an empty list.
    """


def render_proposal_markdown(req: CreateProposalRequest, source: str) -> str:
    """Render the issue body matching the board-proposal form template, in form order."""
    repos_str = ", ".join(req.target_repos)
    code_request = req.code_request_url or "_No response_"

    return f"""### Submitter / Source
{source}

### Target Repository / Repositories
{repos_str}

### Problem Statement
{req.problem}

### Evidence and Context
{req.evidence}

### Options Considered
{req.options_considered}

### Submitter's Lean
{req.lean}

### Estimated Effort
{req.estimated_cost}

### Urgency
{req.urgency}

### Linked Code Request or Issue (Optional)
{code_request}
"""


def parse_proposal_markdown(body: str) -> dict[str, Any]:
    """Extract structured fields from a proposal issue body.

    Handles both agent-submitted bodies (``render_proposal_markdown`` above)
    and bodies filed directly through the GitHub issue form, which renders an
    empty optional field as the literal text ``_No response_``.
    """
    if not body:
        return {}

    sections: dict[str, str] = {}
    lines = body.splitlines()
    current_header = ""
    current_lines: list[str] = []

    for line in lines:
        match = _SECTION_RE.match(line)
        if match:
            if current_header:
                sections[current_header] = "\n".join(current_lines).strip()
            current_header = match.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)

    if current_header:
        sections[current_header] = "\n".join(current_lines).strip()

    target_repos_raw = sections.get("target repository / repositories", "")
    target_repos = [r.strip() for r in target_repos_raw.split(",") if r.strip()]

    code_req_raw = sections.get("linked code request or issue (optional)", "")
    code_request_url = None if code_req_raw.strip().lower() in ("", _NO_RESPONSE, "none", "n/a") else code_req_raw

    source_raw = sections.get("submitter / source", "")
    source = None if source_raw.strip().lower() in ("", _NO_RESPONSE) else source_raw

    return {
        "target_repos": target_repos,
        "problem": sections.get("problem statement", ""),
        "evidence": sections.get("evidence and context", ""),
        "options_considered": sections.get("options considered", ""),
        "lean": sections.get("submitter's lean", ""),
        "estimated_cost": sections.get("estimated effort", ""),
        "urgency": sections.get("urgency", ""),
        "source": source or "human",
        "code_request_url": code_request_url,
    }


def extract_decision_info(issue: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Determine decision outcome and decision labels from an issue dictionary.

    Only ``_KNOWN_DECISIONS`` labels count as a decision outcome (review
    #1444 defect 4) — `board:needs-info` and any other `board:*` label are
    left out of both the outcome and the decision-label list.
    """
    labels = [lbl.get("name", "") if isinstance(lbl, dict) else str(lbl) for lbl in issue.get("labels", [])]
    decision_labels: list[str] = []
    decision: str | None = None

    for lbl in labels:
        if lbl in _KNOWN_DECISIONS:
            decision_labels.append(lbl)
            if decision is None:
                decision = _KNOWN_DECISIONS[lbl]

    # Closed issues without explicit outcome label count as decided.
    if not decision and issue.get("state") == "closed":
        decision = "decided"

    return decision, decision_labels


async def create_github_proposal(
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new proposal issue in Repository_Management."""
    issue_labels = list(labels or [PROPOSAL_LABEL, NEEDS_DECISION_LABEL])
    if PROPOSAL_LABEL not in issue_labels:
        issue_labels.append(PROPOSAL_LABEL)
    if NEEDS_DECISION_LABEL not in issue_labels:
        issue_labels.append(NEEDS_DECISION_LABEL)

    endpoint = f"repos/{RM_REPO}/issues"
    payload = {
        "title": title,
        "body": body,
        "labels": issue_labels,
    }
    log.info("Creating Board Proposal in %s: %s", RM_REPO, title)
    return await gh_api_write(endpoint, "POST", payload)


async def list_github_proposals(state: str = "all") -> list[dict[str, Any]]:
    """List proposals from GitHub issues with label board:proposal.

    Paginates through every page instead of relying on a single
    ``per_page=100`` request (DRY with ``gh_client.paginate``; review #1444
    defect 7). Never swallows a fetch failure — raises ``ProposalStoreError``
    so the caller (``proposals.service``) can fail closed (review #1444
    defect 3) instead of treating an unreadable GitHub as "no proposals".
    """
    endpoint = f"repos/{RM_REPO}/issues?labels={PROPOSAL_LABEL}&state={state}"
    log.debug("Listing Board Proposals from %s (state=%s)", RM_REPO, state)

    try:
        import gh_client as _gc
    except ImportError:
        _gc = None  # type: ignore[assignment]

    if _gc is not None:
        try:
            return [item async for item in _gc.paginate(endpoint)]
        except _gc.GhAuthError:
            pass  # no token configured — fall back to the gh_api subprocess path below
        except Exception as exc:
            log.warning("Failed fetching proposals from GitHub: %s", exc)
            raise ProposalStoreError(f"Failed to list proposals from {RM_REPO}: {exc}") from exc

    data = await gh_api(f"{endpoint}&per_page=100")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        return items if isinstance(items, list) else []
    raise ProposalStoreError(f"Unexpected GitHub response shape for {endpoint}: {type(data).__name__}")


async def get_github_proposal(number: int) -> dict[str, Any]:
    """Fetch a single proposal issue by number."""
    endpoint = f"repos/{RM_REPO}/issues/{number}"
    log.debug("Fetching proposal #%d from %s", number, RM_REPO)
    res = await gh_api(endpoint)
    return res if isinstance(res, dict) else {}


async def get_github_proposal_comments(number: int) -> list[dict[str, Any]]:
    """Fetch comments on a proposal issue.

    Raises ``ProposalStoreError`` on failure rather than returning an empty
    list (review #1444 defect 3) — a Board-Secretary comment thread that
    fails to load is not the same thing as a proposal with no comments.
    """
    endpoint = f"repos/{RM_REPO}/issues/{number}/comments"
    log.debug("Fetching comments for proposal #%d from %s", number, RM_REPO)
    try:
        data = await gh_api(endpoint)
    except Exception as exc:
        log.warning("Failed fetching comments for proposal #%d: %s", number, exc)
        raise ProposalStoreError(f"Failed to fetch comments for proposal #{number}: {exc}") from exc
    return data if isinstance(data, list) else []
