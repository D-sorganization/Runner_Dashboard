"""Code Request package (CR-2, issue #1282)."""

from __future__ import annotations

from code_requests.lifecycle import (
    LEGAL_TRANSITIONS,
    PRE_PLANNING_STATES,
    InvalidTransitionError,
    is_legal_transition,
    transition,
)
from code_requests.model import (
    BoardRoute,
    CodeRequest,
    CodeRequestAuditEvent,
    CodeRequestState,
    Requester,
    RequesterKind,
    code_request_from_issue,
    parse_issue_body,
    serialize_front_matter,
    serialize_issue_body,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "PRE_PLANNING_STATES",
    "BoardRoute",
    "CodeRequest",
    "CodeRequestAuditEvent",
    "CodeRequestState",
    "InvalidTransitionError",
    "Requester",
    "RequesterKind",
    "code_request_from_issue",
    "is_legal_transition",
    "parse_issue_body",
    "serialize_front_matter",
    "serialize_issue_body",
    "transition",
]
