"""Contract and apply-path tests for the repository ruleset supplement (tsc + detect-secrets)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import apply_supplemental_required_checks_ruleset as apply  # noqa: E402

_POLICY = _ROOT / "config" / "required_status_checks_policy.json"
_SUPPLEMENT = _ROOT / "config" / "repository_rulesets" / "required-status-checks-supplement.json"
_LIVE_CONTEXTS = Path(__file__).parent / "contracts" / "required_status_checks_live_contexts.json"


def _load(name: Path):
    return json.loads(name.read_text(encoding="utf-8"))


def test_live_context_fixture_matches_workflow_job_names() -> None:
    """Guard against invented check names: fixture must match workflow display names."""
    live = _load(_LIVE_CONTEXTS)
    policy = _load(_POLICY)
    policy_names = {entry["context"] for entry in policy["required_contexts"]}
    for name in live["check_run_names"]:
        if name == "quality-gate":
            continue
        assert name in policy_names, f"{name!r} missing from required_status_checks_policy.json"


def test_supplement_contexts_are_subset_of_policy() -> None:
    policy = _load(_POLICY)
    supplement = _load(_SUPPLEMENT)
    policy_contexts = {entry["context"] for entry in policy["required_contexts"]}
    supplement_contexts = set(apply.required_contexts_from_supplement(supplement))
    assert supplement_contexts <= policy_contexts
    assert supplement_contexts == {
        "TypeScript typecheck (tsc)",
        "detect-secrets (baseline diff)",
    }


def test_validate_supplement_document_rejects_empty_checks() -> None:
    document = _load(_SUPPLEMENT)
    document["rules"][0]["parameters"]["required_status_checks"] = []
    problems = apply.validate_supplement_document(document)
    assert any("required_status_checks" in p for p in problems)


def test_build_ruleset_payload_strips_repo_metadata() -> None:
    document = _load(_SUPPLEMENT)
    payload = apply.build_github_ruleset_payload(document)
    assert payload["name"] == document["ruleset_name"]
    assert "ruleset_name" not in payload
    assert payload["target"] == "branch"
    assert payload["rules"][0]["type"] == "required_status_checks"


def test_upsert_plan_creates_when_missing() -> None:
    document = _load(_SUPPLEMENT)
    plan = apply.plan_upsert(document, existing_rulesets=[])
    assert plan.action == "create"
    assert plan.ruleset_id is None


def test_upsert_plan_updates_when_name_matches() -> None:
    document = _load(_SUPPLEMENT)
    existing = [{"id": 999, "name": document["ruleset_name"], "enforcement": "active"}]
    plan = apply.plan_upsert(document, existing_rulesets=existing)
    assert plan.action == "update"
    assert plan.ruleset_id == 999


@pytest.mark.parametrize(
    "token",
    ["", "   "],
)
def test_apply_precondition_rejects_missing_token(token: str) -> None:
    with pytest.raises(apply.PreconditionError, match="GH_TOKEN"):
        apply.require_admin_token(token)
