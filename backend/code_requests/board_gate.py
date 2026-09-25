"""Board routing gate for new or significant Code Requests (CR-6, issue #1286).

Decides whether a Code Request must go through Architecture Board review before planning.
Routes new capabilities, cross-repo contracts, confidential data egress, and significant
expansions to the Board; routine work proceeds directly to planning.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from code_requests.lifecycle import transition
from code_requests.model import (
    BoardRoute,
    CodeRequest,
    CodeRequestState,
)
from pydantic import BaseModel, Field


class BoardRoutingCriteria(BaseModel):
    """Explicit requester-declared or system-extracted routing flags."""

    new_surface: bool = False  # New page, tab, public section, or app/tool
    new_service_or_repo: bool = False  # New repo, service, or long-running daemon
    new_dependency_or_egress: bool = False  # New external dep, 3rd party API, data egress
    inentec_data_egress: bool = False  # Confidential InEnTec/ICR data egress (needs user signoff)
    cross_repo_contract: bool = False  # Dispatch envelope, fleet-managed, shared modules
    public_site_structure: bool = False  # Structural change to public site (e.g. AffineDrift IA)
    estimated_child_issues: int = 0  # > 8 child issues triggers post-planning gate
    target_repos_count: int = 1  # > 1 repo triggers gate
    tagged_board: bool = False  # Explicitly tagged `board`


class BoardRoutingDecision(BaseModel):
    """Structured decision output from the Board routing gate."""

    routes_to_board: bool
    reasons: list[str] = Field(default_factory=list)
    criteria_matched: list[str] = Field(default_factory=list)
    requires_user_signoff: bool = False


class BoardClassifier(Protocol):
    """Protocol for heuristic or LLM-based text classifiers."""

    def classify(self, title: str, prompt: str) -> BoardRoutingDecision:
        """Evaluate title and prompt for board-routing triggers."""
        ...


class RuleBasedBoardClassifier:
    """Heuristic rule-based classifier evaluating title and prompt text."""

    SURFACE_KEYWORDS: list[re.Pattern[str]] = [
        re.compile(r"\b(new\s+(?:page|tab|screen|surface|app|tool|dashboard))\b", re.I),
        re.compile(r"\b(brand\s+new\s+\w+\s+(?:tab|page))\b", re.I),
    ]

    SERVICE_KEYWORDS: list[re.Pattern[str]] = [
        re.compile(r"\b(new\s+(?:repo|repository|service|daemon|microservice))\b", re.I),
    ]

    CONTRACT_KEYWORDS: list[re.Pattern[str]] = [
        re.compile(r"\b(?:dispatch\s+envelope|fleet-managed|cross-repo\s+contract)\b", re.I),
        re.compile(r"\b(?:envelope\s+schema)\b", re.I),
    ]

    EGRESS_KEYWORDS: list[re.Pattern[str]] = [
        re.compile(r"\b(inentec|icr)\b", re.I),
        re.compile(r"\b(?:confidential\s+data\s+egress|external\s+data\s+egress)\b", re.I),
    ]

    def classify(self, title: str, prompt: str) -> BoardRoutingDecision:
        """Scan title and prompt for key board routing triggers."""
        text = f"{title}\n{prompt}"
        matched: list[str] = []
        reasons: list[str] = []
        user_signoff = False

        for pattern in self.SURFACE_KEYWORDS:
            if pattern.search(text):
                matched.append("new_surface")
                reasons.append("Adds a new user-facing surface (page/tab/app)")
                break

        for pattern in self.SERVICE_KEYWORDS:
            if pattern.search(text):
                matched.append("new_service_or_repo")
                reasons.append("Creates a new repository, service, or daemon")
                break

        for pattern in self.CONTRACT_KEYWORDS:
            if pattern.search(text):
                matched.append("cross_repo_contract")
                reasons.append("Modifies a cross-repo contract or schema")
                break

        for pattern in self.EGRESS_KEYWORDS:
            if pattern.search(text):
                matched.append("inentec_data_egress")
                reasons.append("Confidential InEnTec/ICR data egress (mandatory user sign-off)")
                user_signoff = True
                break

        return BoardRoutingDecision(
            routes_to_board=bool(matched),
            reasons=reasons,
            criteria_matched=matched,
            requires_user_signoff=user_signoff,
        )


def evaluate_board_routing(
    request: CodeRequest,
    criteria: BoardRoutingCriteria | None = None,
    classifier: BoardClassifier | None = None,
) -> BoardRoutingDecision:
    """Pure evaluation of whether a Code Request must route to the Board.

    Follows the priority:
    1. If inentec_data_egress is present: ALWAYS routes to Board with requires_user_signoff=True.
    2. If operator specified FORCE_BOARD: routes to Board.
    3. If operator specified SKIP_BOARD: skips Board.
    4. Evaluates explicit criteria and classifier.
    """
    crit = criteria or BoardRoutingCriteria()

    # Rule 0 (Critical Security/Confidentiality): InEnTec/ICR data egress ALWAYS requires Board + User signoff
    if crit.inentec_data_egress:
        return BoardRoutingDecision(
            routes_to_board=True,
            reasons=["Confidential InEnTec/ICR data egress requires Board review and user sign-off."],
            criteria_matched=["inentec_data_egress"],
            requires_user_signoff=True,
        )

    # Operator overrides
    if request.board_route == BoardRoute.FORCE_BOARD:
        return BoardRoutingDecision(
            routes_to_board=True,
            reasons=["Operator force_board override active."],
            criteria_matched=["force_board"],
            requires_user_signoff=False,
        )

    if request.board_route == BoardRoute.SKIP_BOARD:
        return BoardRoutingDecision(
            routes_to_board=False,
            reasons=["Operator skip_board override active."],
            criteria_matched=["skip_board"],
            requires_user_signoff=False,
        )

    # Evaluate explicit criteria
    matched: list[str] = []
    reasons: list[str] = []

    if crit.new_surface:
        matched.append("new_surface")
        reasons.append("Adds a new user-facing surface (page, tab, or tool).")

    if crit.new_service_or_repo:
        matched.append("new_service_or_repo")
        reasons.append("Creates a new repository, service, or daemon.")

    if crit.new_dependency_or_egress:
        matched.append("new_dependency_or_egress")
        reasons.append("Adds external dependencies or third-party data egress.")

    if crit.cross_repo_contract:
        matched.append("cross_repo_contract")
        reasons.append("Modifies cross-repo contract or schema.")

    if crit.public_site_structure:
        matched.append("public_site_structure")
        reasons.append("Structural changes to public site navigation or architecture.")

    if crit.estimated_child_issues > 8:
        matched.append("estimated_child_issues")
        reasons.append(f"Estimated at {crit.estimated_child_issues} child issues (> 8).")

    if crit.target_repos_count > 1:
        matched.append("multiple_target_repos")
        reasons.append(f"Spans {crit.target_repos_count} target repositories (> 1).")

    if crit.tagged_board:
        matched.append("tagged_board")
        reasons.append("Requester explicitly tagged proposal for Board review.")

    # If no explicit criteria matched, run the classifier pass
    if not matched:
        cls = classifier or RuleBasedBoardClassifier()
        classified = cls.classify(request.title, request.prompt)
        if classified.routes_to_board:
            matched.extend(classified.criteria_matched)
            reasons.extend(classified.reasons)
            if classified.requires_user_signoff:
                return classified

    return BoardRoutingDecision(
        routes_to_board=bool(matched),
        reasons=reasons,
        criteria_matched=matched,
        requires_user_signoff=False,
    )


def route_code_request_to_board(
    request: CodeRequest,
    decision: BoardRoutingDecision,
    create_proposal_fn: Callable[..., Any],
    actor: str = "board-gate",
) -> CodeRequest:
    """Create a Board proposal via CR-7 API and transition Code Request to board_review."""
    code_req_url = f"https://github.com/D-sorganization/Runner_Dashboard/issues/{request.issue_number}"
    reasons_str = "; ".join(decision.reasons) or "Significant architectural change"

    # Pre-fill proposal fields
    proposal = create_proposal_fn(
        title=request.title,
        target_repos=[request.repository],
        problem=request.prompt,
        options_considered=f"1. Implement as requested: {request.title}\n2. Reject or defer based on Board review.",
        submitter_lean=f"Routed automatically to Board: {reasons_str}",
        estimated_effort="Medium",
        urgency="Normal",
        code_request_url=code_req_url,
        source=request.requester.id,
    )

    prop_num = str(getattr(proposal, "number", proposal.get("number") if isinstance(proposal, dict) else proposal))

    updated = transition(
        request,
        CodeRequestState.BOARD_REVIEW,
        actor=actor,
        reason=f"Routed to Board ({reasons_str}) -> Proposal #{prop_num}",
    )
    updated.board_proposal = prop_num
    return updated


def sync_board_proposal_decision(
    request: CodeRequest,
    get_proposal_fn: Callable[[int], dict[str, Any]],
    actor: str = "board-sync",
) -> CodeRequest:
    """Check decision on linked board proposal and transition Code Request accordingly.

    On board:accepted -> moves to PLANNING, appending Board notes to prompt.
    On board:deferred -> moves to DEFERRED.
    On board:declined -> moves to DECLINED.
    """
    if not request.board_proposal:
        return request

    prop_num = int(request.board_proposal)
    proposal_data = get_proposal_fn(prop_num)

    decision = proposal_data.get("decision")
    decision_labels = proposal_data.get("decision_labels", [])

    if "board:accepted" in decision_labels or decision == "accepted":
        comments = proposal_data.get("comments", [])
        secretary_notes = [
            c.get("body", "") for c in comments if "secretary" in str(c.get("user", "")).lower() or c.get("body")
        ]
        notes_text = "\n\n".join(secretary_notes).strip()

        updated_prompt = request.prompt
        if notes_text:
            updated_prompt = f"{request.prompt}\n\n### Board Review Notes\n{notes_text}"

        updated = transition(
            request,
            CodeRequestState.PLANNING,
            actor=actor,
            reason=f"Board accepted proposal #{prop_num}.",
        )
        updated.prompt = updated_prompt
        return updated

    if "board:declined" in decision_labels or decision == "declined":
        return transition(
            request,
            CodeRequestState.DECLINED,
            actor=actor,
            reason=f"Board declined proposal #{prop_num}.",
        )

    if "board:deferred" in decision_labels or decision == "deferred":
        return transition(
            request,
            CodeRequestState.DEFERRED,
            actor=actor,
            reason=f"Board deferred proposal #{prop_num}.",
        )

    return request


def check_board_escalation(
    request: CodeRequest,
    meetings_elapsed: int,
    max_meetings: int = 2,
) -> bool:
    """Return True if the request has waited past the max scheduled meetings deadline."""
    if request.state != CodeRequestState.BOARD_REVIEW:
        return False
    return meetings_elapsed >= max_meetings
