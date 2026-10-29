"""Unit tests for Barb Operating Manual & Playbooks schema, assignment-state enum, and DRY anchor linter (#3486)."""

from __future__ import annotations

import pytest
from staff.playbooks import (
    ALLOWED_ASSIGNMENT_TRANSITIONS,
    CLAIM_ENDPOINT,
    CLAIM_RELEASE_ENDPOINT,
    DB_IS_REBUILDABLE_CACHE,
    HOLDS_AUTHORITY_MODULES,
    SOURCE_OF_TRUTH_MODE,
    TERMINAL_ASSIGNMENT_STATES,
    BarbOperatingManual,
    PlaybookAssignmentState,
    PlaybookDefinition,
    PlaybookValidationError,
    assert_playbooks_clean,
    compute_content_sha256,
    lint_playbook_against_manual,
    parse_operating_manual,
    parse_playbook,
    validate_assignment_transition,
)


def _valid_manual_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": "1.0.0",
        "title": "Barb Operating Manual",
        "sections": [
            {
                "anchor": "claim-before-touching",
                "title": "Claim Before Touching",
                "body": "Every worker must acquire an exclusive issue lease via POST /claims before pushing or arming.",
            },
            {
                "anchor": "pr-gates-and-sizing",
                "title": "PR Gates and Sizing",
                "body": "Pull requests must stay non-draft, under 500 changed lines, and branched from main.",
            },
        ],
    }


def _valid_playbook_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "playbook_id": "overnight-codex-swarm",
        "version": "1.2.0",
        "title": "Overnight Codex Swarm Playbook",
        "summary": "Run bounded overnight swarm lanes following manual anchors.",
        "manual_anchors": ["#claim-before-touching", "#pr-gates-and-sizing"],
        "directive_refs": ["fleet-standards", "overload-practice"],
        "role_refs": ["barb", "codex-worker"],
        "parameters": [
            {"name": "max_lanes", "param_type": "integer", "required": True, "default": 4},
            {"name": "mode", "param_type": "enum", "required": False, "choices": ["standard", "drain"]},
        ],
        "steps": ["Follow manual#claim-before-touching and enforce manual#pr-gates-and-sizing."],
    }


def test_shared_contract_and_assignment_state_enum() -> None:
    assert SOURCE_OF_TRUTH_MODE == "repo_markdown_yaml_cache_only" and DB_IS_REBUILDABLE_CACHE is True
    assert (CLAIM_ENDPOINT, CLAIM_RELEASE_ENDPOINT) == ("POST /claims", "POST /claims/release")
    assert HOLDS_AUTHORITY_MODULES == ("backend/staff/holds.py", "backend/staff/hold_actions.py")
    expected = {"ASSIGNED", "ACKNOWLEDGED", "RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "ORPHANED"}
    assert {s.value for s in PlaybookAssignmentState} == expected
    assert validate_assignment_transition("ASSIGNED", "ACKNOWLEDGED") == PlaybookAssignmentState.ACKNOWLEDGED
    assert validate_assignment_transition("ACKNOWLEDGED", "RUNNING") == PlaybookAssignmentState.RUNNING
    assert validate_assignment_transition("RUNNING", "COMPLETED") == PlaybookAssignmentState.COMPLETED
    assert validate_assignment_transition("ASSIGNED", "TIMED_OUT") == PlaybookAssignmentState.TIMED_OUT
    assert validate_assignment_transition("RUNNING", "ORPHANED") == PlaybookAssignmentState.ORPHANED
    for terminal in TERMINAL_ASSIGNMENT_STATES:
        assert ALLOWED_ASSIGNMENT_TRANSITIONS[terminal] == frozenset()
        with pytest.raises(PlaybookValidationError, match="transition"):
            validate_assignment_transition(terminal, PlaybookAssignmentState.RUNNING)
    with pytest.raises(PlaybookValidationError, match="transition"):
        validate_assignment_transition("ASSIGNED", "COMPLETED")


def test_parse_valid_manual_playbook_and_frontmatter() -> None:
    manual, pb = parse_operating_manual(_valid_manual_dict()), parse_playbook(_valid_playbook_dict())
    assert isinstance(manual, BarbOperatingManual) and manual.section_by_anchor("#claim-before-touching") is not None
    assert isinstance(pb, PlaybookDefinition) and pb.pin == f"overnight-codex-swarm@1.2.0+{pb.sha256[:12]}"
    md = (
        "---\nschema_version: 1\nplaybook_id: dots-barb-brief\nversion: 1.0.0\ntitle: Dots\nsummary: S.\n"
        "manual_anchors: [claim-before-touching]\ndirective_refs: [fleet-standards]\nrole_refs: [barb]\n---\n"
        "1. Follow manual#claim-before-touching via POST /claims.\n"
    )
    assert parse_playbook(md).playbook_id == "dots-barb-brief" and len(compute_content_sha256("hello")) == 64


def test_invalid_fixtures_and_linter_enforcement() -> None:
    with pytest.raises(PlaybookValidationError, match="POST /claims"):
        parse_operating_manual({**_valid_manual_dict(), "claim_endpoint": "POST /api/coordination/claims"})
    with pytest.raises(PlaybookValidationError, match="POST /claims"):
        parse_playbook({**_valid_playbook_dict(), "claim_endpoint": "/api/coordination/claims"})
    with pytest.raises(PlaybookValidationError, match="hold"):
        parse_playbook({**_valid_playbook_dict(), "lift_hold": "#2657"})
    with pytest.raises(PlaybookValidationError, match="choices"):
        parse_playbook({**_valid_playbook_dict(), "parameters": [{"name": "mode", "param_type": "enum"}]})

    manual, valid_pb = parse_operating_manual(_valid_manual_dict()), parse_playbook(_valid_playbook_dict())
    assert lint_playbook_against_manual(valid_pb, manual) == ()
    assert_playbooks_clean([valid_pb], manual)

    broken_pb = parse_playbook(
        {**_valid_playbook_dict(), "manual_anchors": ["missing-anchor"], "steps": ["Follow manual#missing-inline."]}
    )
    assert {i.code for i in lint_playbook_against_manual(broken_pb, manual)} == {"BROKEN_ANCHOR"}
    with pytest.raises(PlaybookValidationError, match="BROKEN_ANCHOR"):
        assert_playbooks_clean([broken_pb], manual)

    copied_steps = [
        "Every worker must acquire an exclusive issue lease via POST /claims before pushing or arming.",
        "Do not call /api/coordination/claims.",
    ]
    copied_pb = parse_playbook({**_valid_playbook_dict(), "steps": copied_steps})
    assert {i.code for i in lint_playbook_against_manual(copied_pb, manual)} == {
        "COPIED_POLICY_TEXT",
        "DEPRECATED_CLAIM_ENDPOINT",
    }
    with pytest.raises(PlaybookValidationError, match="COPIED_POLICY_TEXT"):
        assert_playbooks_clean([copied_pb], manual)
