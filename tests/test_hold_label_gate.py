"""Hold labels block queue admission (Repository_Management #3091, Tools #6036)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.hold_label_gate import (
    HoldLabelError,
    assert_clear,
    blocking_hold_labels,
    labels_from_json,
    main,
    pull_requests_from_merge_group,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_clean_labels_do_not_block() -> None:
    assert blocking_hold_labels(["spec-exempt", "agent:cursor-agent"]) == ()
    assert_clear(["spec-exempt"], context="PR #1")


@pytest.mark.parametrize(
    "label",
    ["do-not-merge", "needs-owner", "hold", "Do-Not-Merge", "HOLD"],
)
def test_named_hold_labels_block(label: str) -> None:
    assert blocking_hold_labels([label]) == (label,)
    with pytest.raises(HoldLabelError, match="carries hold label"):
        assert_clear([label], context="PR #6036")


def test_merge_group_ref_and_grouped_message_name_every_pr() -> None:
    numbers = pull_requests_from_merge_group(
        "gh-readonly-queue/main/pr-6036-deadbeef",
        "* #6036\n* Pull request #6037\nSee #9999 in the body\n",
    )
    assert numbers == (6036, 6037)


def test_merge_group_without_a_pr_number_fails_closed() -> None:
    assert pull_requests_from_merge_group("refs/heads/main", "ordinary commit") == ()
    assert main(["--extract-prs", "--head-ref", "refs/heads/main"]) == 1


def test_labels_json_rejects_a_missing_or_shaped_payload() -> None:
    with pytest.raises(HoldLabelError):
        labels_from_json("not-json")
    with pytest.raises(HoldLabelError):
        labels_from_json('{"name": "do-not-merge"}')
    assert main(["--labels-json", '["needs-owner"]', "--context", "PR #7"]) == 1
    assert main(["--labels-json", "[]", "--context", "PR #7"]) == 0


def _workflow() -> dict:
    import yaml

    path = REPO_ROOT / ".github" / "workflows" / "ci-standard.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_pull_request_trigger_reacts_to_label_events() -> None:
    # PyYAML parses the bare key ``on`` as boolean True.
    triggers = _workflow().get("on") or _workflow()[True]
    types = set(triggers["pull_request"]["types"])
    assert {"labeled", "unlabeled", "opened", "synchronize", "reopened"} <= types


def test_gate_steps_use_python3_not_bare_python() -> None:
    jobs = _workflow()["jobs"]
    for name in ("hold-label-gate", "quality-gate"):
        for step in jobs[name]["steps"]:
            run = step.get("run", "")
            assert "hold_label_gate" not in run or "python3 " in run, name
            assert "python scripts/hold_label_gate" not in run, name


def test_quality_gate_rechecks_labels_last_on_merge_group() -> None:
    steps = _workflow()["jobs"]["quality-gate"]["steps"]
    final = steps[-1]
    assert "hold_label_gate.py --check-event" in final["run"]
    assert "merge_group" in final["if"]


def test_quality_gate_depends_on_the_hold_job() -> None:
    needs = _workflow()["jobs"]["quality-gate"]["needs"]
    assert "hold-label-gate" in needs


def test_check_event_reads_live_labels_for_each_queued_pr() -> None:
    from scripts.hold_label_gate import check_event

    seen: list[int] = []

    def fake(number: int) -> str:
        seen.append(number)
        return '["hold"]' if number == 8 else "[]"

    with pytest.raises(HoldLabelError, match="#8"):
        check_event(
            "merge_group",
            labels_json="",
            head_ref="gh-readonly-queue/main/pr-7-abc",
            message="* #8",
            fetch_labels=fake,
        )
    assert seen == [7, 8]


def test_check_event_pull_request_uses_payload_labels() -> None:
    from scripts.hold_label_gate import check_event

    check_event("pull_request", labels_json="[]", head_ref="", message="")
    with pytest.raises(HoldLabelError):
        check_event("pull_request", labels_json='["needs-owner"]', head_ref="", message="")
