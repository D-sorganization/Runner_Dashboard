from __future__ import annotations

from unittest.mock import MagicMock

from backend.backtest_decider_dataset import (
    DeciderClient,
    evaluate_backtest_case,
    is_eligible_unclassified_issue,
    sanitize_issue_payload,
)
from scripts.sanitize_private_topology import LITERAL_REPLACEMENTS


def test_is_eligible_unclassified_issue_filters_explicit_tier() -> None:
    assert not is_eligible_unclassified_issue(["tier:cli"], "fix minor typo")
    assert not is_eligible_unclassified_issue(["tier:strong"], "implement auth")


def test_is_eligible_unclassified_issue_filters_strong_flags() -> None:
    assert not is_eligible_unclassified_issue(["security"], "update dependency")
    assert not is_eligible_unclassified_issue(["type:epic"], "new feature")
    assert not is_eligible_unclassified_issue(["panel-review"], "review request")


def test_is_eligible_unclassified_issue_filters_dictated_fix() -> None:
    assert not is_eligible_unclassified_issue([], "replace `foo = 1` with `foo = 2`.")


def test_is_eligible_unclassified_issue_accepts_clean_unlabeled() -> None:
    assert is_eligible_unclassified_issue(["bug", "backend"], "investigate flaky test in scheduler")


def _replacement_for(public: str, *, capitalized: bool = False) -> tuple[str, str]:
    """Pick a sanitizer pair by its public example so the test file stays free of live names."""
    for private, replacement in LITERAL_REPLACEMENTS:
        if replacement != public:
            continue
        if capitalized and not private[:1].isupper():
            continue
        return private, replacement
    raise AssertionError(f"no sanitizer pair for {public!r}")


def test_sanitize_issue_payload_strips_private_identifiers() -> None:
    tail_private, tail_public = _replacement_for("hub-a.fleet-example.ts.net")
    title_private, title_public = _replacement_for("hub-a", capitalized=True)
    prompt_private, prompt_public = _replacement_for("node-b", capitalized=True)
    payload = {
        "title": f"Fix crash on {title_private}",
        "body": f"See tailnet host {tail_private}",
        "prompt": f"Investigate {prompt_private} memory usage",
    }
    sanitized = sanitize_issue_payload(payload)
    assert tail_private not in sanitized["body"]
    assert tail_public in sanitized["body"]
    assert title_private not in sanitized["title"]
    assert title_public in sanitized["title"]
    assert prompt_private not in sanitized["prompt"]
    assert prompt_public in sanitized["prompt"]


def test_evaluate_backtest_case_fail_safe_on_unclear() -> None:
    mock_decider = MagicMock(spec=DeciderClient)
    mock_decider.predict.return_value = "unclear"

    res = evaluate_backtest_case(["bug"], "investigate flaky test", decider=mock_decider)
    assert res["current_tier"] == "strong"
    assert res["shadow_tier"] == "strong"
    assert res["advice"] == "unclear"


def test_evaluate_backtest_case_routine_implementation() -> None:
    mock_decider = MagicMock(spec=DeciderClient)
    mock_decider.predict.return_value = "routine_implementation"

    res = evaluate_backtest_case(["bug"], "fix typo in docstring", decider=mock_decider)
    assert res["current_tier"] == "strong"
    assert res["shadow_tier"] == "cli"
    assert res["advice"] == "routine_implementation"


def test_evaluate_backtest_case_design_or_cross_repo() -> None:
    mock_decider = MagicMock(spec=DeciderClient)
    mock_decider.predict.return_value = "design_or_cross_repo"

    res = evaluate_backtest_case(["bug"], "refactor orchestrator", decider=mock_decider)
    assert res["current_tier"] == "strong"
    assert res["shadow_tier"] == "strong"
    assert res["advice"] == "design_or_cross_repo"
