"""Helpers for tests that approve or execute proposals: the hash a reviewer would echo (#1807)."""

from __future__ import annotations

from staff.actions import stored_params_hash
from staff.conversations import get_conversation_store


def reviewed_hash(proposal_id: str) -> str:
    """The hash of the stored proposal, i.e. what GET /proposals/{id} previews."""
    return stored_params_hash(get_conversation_store(), proposal_id)


def hash_query(proposal_id: str) -> dict[str, str]:
    """Query params for POST /proposals/{id}/execute."""
    return {"params_hash": reviewed_hash(proposal_id)}
