"""Unit tests for porting Disciple to an RD persona role (#3704, next slice of epic #3695's
cutover-tracking checklist). Disciple is read-only and unscheduled (``schedule: null``), so
unlike every other #3704 slice it has no capsule/slot/fallback-drill plumbing to test."""

from __future__ import annotations

from pathlib import Path

import pytest
import staff.disciple_persona as dc
from staff.provider_failover_config import FailoverStep
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit


def test_disciple_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert dc.write_disciple_role_yaml(tmp_path).name == "disciple.yml"
    spec = dc.load_disciple_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := dc.load_disciple_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is False
    assert spec_raw["permissions"]["push_branch"] is False
    assert spec_raw["permissions"]["open_pr"] is False
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["schedule"] is None and spec_raw["window"] is None
    assert spec_raw["reports_to"] == "user" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity"]
    assert "retry" not in spec_raw
    clear_roles_cache()
    role = load_roles(tmp_path)["disciple"]
    assert role.valid
    assert role.repos == dc.DISCIPLE_REPOS
    assert role.scope["pack"] == "staff/knowledge/findings.yml"
    assert role.defers_to == ("research-scout", "vision-quest", "fleet-critic")


def test_disciple_rd_barb_provider_chain_is_claude_first() -> None:
    # The real staff/roles/disciple.yml lists three providers (claude, codex, antigravity) with
    # no retry block, so without a static PERSONA_ROLE_CHAINS entry resolve_failover_chain would
    # silently fall back to rd_barb_provider's generic codex-first DEFAULT_CHAIN.
    assert rp.select({}, role="disciple") == rp.Decision("claude", "opus", False)
    expected_chain = (FailoverStep("claude", None), FailoverStep("codex", None), FailoverStep("antigravity", None))
    assert rp.chain({}, role="disciple") == (FailoverStep("claude", "opus"), *expected_chain[1:])


def test_is_valid_citation() -> None:
    assert dc.is_valid_citation("AffineDrift:docs/models/drag.md#reynolds @ 1a2b3c4")
    assert not dc.is_valid_citation("not a citation")
    assert not dc.is_valid_citation("AffineDrift:docs/models/drag.md#reynolds")


def test_answer_disciple_question_answers_from_passages() -> None:
    passages = [
        {
            "text": "Drag coefficient converges above Re=1e5.",
            "citation": "AffineDrift:docs/models/drag.md#reynolds @ 1a2b3c4",
            "source": "findings",
            "status": "current",
        }
    ]
    res = dc.answer_disciple_question(passages)
    assert res["status"] == "answered"
    assert res["citations"] == ["AffineDrift:docs/models/drag.md#reynolds @ 1a2b3c4"]
    assert res["superseded_note"] == ""
    assert res["defers_to"] == ()


def test_answer_disciple_question_defers_when_pack_does_not_cover_it() -> None:
    res = dc.answer_disciple_question([])
    assert res == {
        "status": "not_established",
        "answer": dc.NOT_ESTABLISHED,
        "defers_to": ["research-scout", "vision-quest", "fleet-critic"],
        "citations": [],
    }


def test_answer_disciple_question_names_what_superseded_an_old_result() -> None:
    passages = [
        {
            "text": "Model v1 predicted laminar flow throughout.",
            "citation": "AffineDrift:docs/models/drag.md#v1 @ 0a0a0a0",
            "source": "findings",
            "status": "superseded",
        },
        {
            "text": "Model v2 predicts transition to turbulence above Re=1e5.",
            "citation": "AffineDrift:docs/models/drag.md#v2 @ 1a2b3c4",
            "source": "findings",
            "status": "current",
        },
    ]
    res = dc.answer_disciple_question(passages)
    assert res["status"] == "answered"
    assert res["answer"] == "Model v2 predicts transition to turbulence above Re=1e5."
    assert "Model v1 predicted laminar flow throughout." in res["superseded_note"]
    assert "0a0a0a0" in res["superseded_note"]


def test_answer_disciple_question_surfaces_disagreement_by_authority_instead_of_blending() -> None:
    passages = [
        {
            "text": "Peer review flags the coupling term as unstable.",
            "citation": "UpstreamDrift:docs/reviews/coupling.md#r1 @ aaa1111",
            "source": "reviews",
            "status": "current",
        },
        {
            "text": "The published model treats the coupling term as stable.",
            "citation": "AffineDrift:docs/published/coupling.md#p1 @ bbb2222",
            "source": "published",
            "status": "current",
        },
    ]
    res = dc.answer_disciple_question(passages)
    assert res["status"] == "disagreement"
    assert res["higher_authority_source"] == "published"
    assert res["answer"] == "The published model treats the coupling term as stable."
    assert set(res["citations"]) == {
        "UpstreamDrift:docs/reviews/coupling.md#r1 @ aaa1111",
        "AffineDrift:docs/published/coupling.md#p1 @ bbb2222",
    }


def test_answer_disciple_question_rejects_a_passage_with_no_valid_citation() -> None:
    with pytest.raises(ValueError, match="valid citation"):
        dc.answer_disciple_question(
            [{"text": "No source for this.", "citation": "nope", "source": "findings", "status": "current"}]
        )
