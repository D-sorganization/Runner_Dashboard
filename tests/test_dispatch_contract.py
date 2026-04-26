"""Unit tests for dispatch_contract.py — envelope validation and confirmation gating."""

from dispatch_contract import (  # noqa: E402
    ALLOWLISTED_ACTIONS,
    CommandEnvelope,
    DispatchAccess,
    DispatchConfirmation,
    build_envelope,
    validate_envelope,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIVILEGED_ACTION = "runner.restart"
_READ_ONLY_ACTION = "dashboard.status"


def _make_confirmation(
    approved_by: str = "alice", approved_at: str = "2026-04-23T00:00:00Z"
) -> DispatchConfirmation:
    return DispatchConfirmation(approved_by=approved_by, approved_at=approved_at)


def _base_envelope(
    action: str, confirmation: DispatchConfirmation | None = None
) -> CommandEnvelope:
    return build_envelope(
        action=action,
        source="hub",
        target="node-1",
        requested_by="alice",
        confirmation=confirmation,
    )


# ---------------------------------------------------------------------------
# DispatchConfirmation.from_dict — valid case
# ---------------------------------------------------------------------------


def test_dispatch_confirmation_from_dict_valid() -> None:
    data = {
        "approved_by": "bob",
        "approved_at": "2026-04-23T12:00:00Z",
        "note": "looks good",
    }
    conf = DispatchConfirmation.from_dict(data)
    assert conf.approved_by == "bob"
    assert conf.approved_at == "2026-04-23T12:00:00Z"
    assert conf.note == "looks good"


# ---------------------------------------------------------------------------
# CommandEnvelope.from_dict — round-trip
# ---------------------------------------------------------------------------


def test_command_envelope_from_dict_round_trip() -> None:
    env = _base_envelope(_READ_ONLY_ACTION)
    data = env.to_dict()
    restored = CommandEnvelope.from_dict(data)
    assert restored.action == env.action
    assert restored.source == env.source
    assert restored.target == env.target
    assert restored.requested_by == env.requested_by
    assert restored.envelope_id == env.envelope_id


# ---------------------------------------------------------------------------
# validate_envelope — READ_ONLY action (no confirmation required)
# ---------------------------------------------------------------------------


def test_validate_envelope_read_only_no_confirmation_accepted() -> None:
    env = _base_envelope(_READ_ONLY_ACTION)
    result = validate_envelope(env)
    assert result.accepted is True
    assert result.action is not None
    assert result.action.access is DispatchAccess.READ_ONLY


# ---------------------------------------------------------------------------
# validate_envelope — PRIVILEGED action + None confirmation → rejected
# ---------------------------------------------------------------------------


def test_validate_envelope_privileged_no_confirmation_rejected() -> None:
    env = _base_envelope(_PRIVILEGED_ACTION, confirmation=None)
    result = validate_envelope(env)
    assert result.accepted is False
    assert result.confirmation_required is True
    assert "confirmation" in result.reason.lower()


# ---------------------------------------------------------------------------
# validate_envelope — PRIVILEGED action + valid confirmation → accepted
# ---------------------------------------------------------------------------


def test_validate_envelope_privileged_valid_confirmation_accepted() -> None:
    conf = _make_confirmation()
    env = _base_envelope(_PRIVILEGED_ACTION, confirmation=conf)
    result = validate_envelope(env)
    assert result.accepted is True


# ---------------------------------------------------------------------------
# validate_envelope — PRIVILEGED action + empty approved_by → rejected
# ---------------------------------------------------------------------------


def test_validate_envelope_privileged_empty_approved_by_rejected() -> None:
    conf = DispatchConfirmation(approved_by="   ", approved_at="2026-04-23T00:00:00Z")
    env = _base_envelope(_PRIVILEGED_ACTION, confirmation=conf)
    result = validate_envelope(env)
    assert result.accepted is False
    assert "approved_by" in result.reason


# ---------------------------------------------------------------------------
# validate_envelope — unknown action name → rejected
# ---------------------------------------------------------------------------


def test_validate_envelope_unknown_action_rejected() -> None:
    env = build_envelope(
        action="totally.unknown.action",
        source="hub",
        target="node-1",
        requested_by="alice",
    )
    result = validate_envelope(env)
    assert result.accepted is False
    assert "allowlisted" in result.reason or "not allowlisted" in result.reason.lower()


# ---------------------------------------------------------------------------
# Sanity: all allowlisted actions are retrievable and have expected types
# ---------------------------------------------------------------------------


def test_allowlisted_actions_have_expected_access_levels() -> None:
    for name, action in ALLOWLISTED_ACTIONS.items():
        assert action.name == name
        assert action.access in (DispatchAccess.READ_ONLY, DispatchAccess.PRIVILEGED)
        if action.access is DispatchAccess.PRIVILEGED:
            assert action.requires_confirmation is True
