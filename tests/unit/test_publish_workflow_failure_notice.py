"""CI failure must be visible on main, schedule and merge_group (#4299)."""

from __future__ import annotations

from scripts.ci.publish_workflow_failure_notice import (
    NoticePublisher,
    build_notice_body,
    build_notice_title,
    notice_title_ref,
    should_publish_notice,
)


def test_should_publish_notice_on_main_push_schedule_and_merge_group() -> None:
    assert should_publish_notice("schedule", "refs/heads/main") is True
    assert should_publish_notice("push", "refs/heads/main") is True
    assert should_publish_notice("merge_group", "gh-readonly-queue/head") is True
    assert should_publish_notice("pull_request", "refs/pull/1/head") is False
    assert should_publish_notice("push", "refs/heads/feature/x") is False


def test_build_notice_title_includes_workflow() -> None:
    title = build_notice_title("CI Standard", "refs/heads/main")
    assert "CI Standard" in title
    assert "refs/heads/main" in title


def test_merge_group_queue_refs_share_notice_title() -> None:
    ref_a = "refs/heads/gh-readonly-queue/main/pr-4478-aaa111"
    ref_b = "refs/heads/gh-readonly-queue/main/pr-4478-bbb222"
    stable = notice_title_ref("merge_group", ref_a, base_ref="refs/heads/main")
    title_a = build_notice_title("CI Standard", stable)
    title_b = build_notice_title(
        "CI Standard",
        notice_title_ref("merge_group", ref_b, base_ref="refs/heads/main"),
    )
    assert title_a == title_b
    assert title_a == "[CI failure] CI Standard on refs/heads/main"


def test_publish_merge_group_dedupes_on_base_ref_not_queue_ref() -> None:
    created: list[dict[str, str]] = []
    comments: list[tuple[int, str]] = []
    searches: list[str] = []

    def search_open(*, repo: str, title: str) -> list[dict[str, str]]:
        searches.append(title)
        if created:
            return [{"number": 77, "title": title}]
        return []

    publisher = NoticePublisher(
        search_open=search_open,
        create_issue=lambda **kwargs: created.append(kwargs) or 77,
        comment_issue=lambda number, body: comments.append((number, body)),
    )
    run_url_a = "https://github.com/D-sorganization/Runner_Dashboard/actions/runs/100"
    queue_ref_a = "refs/heads/gh-readonly-queue/main/pr-1-sha1"
    publisher.publish(
        workflow="CI Standard",
        ref=queue_ref_a,
        run_id="100",
        repository="D-sorganization/Runner_Dashboard",
        run_url=run_url_a,
        event_name="merge_group",
        base_ref="refs/heads/main",
    )
    queue_ref_b = "refs/heads/gh-readonly-queue/main/pr-2-sha2"
    publisher.publish(
        workflow="CI Standard",
        ref=queue_ref_b,
        run_id="101",
        repository="D-sorganization/Runner_Dashboard",
        run_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/101",
        event_name="merge_group",
        base_ref="refs/heads/main",
    )
    assert len(created) == 1
    assert len(comments) == 1
    assert queue_ref_a in created[0]["body"]
    assert queue_ref_b in comments[0][1]
    assert searches[0] == searches[1]


def test_publish_creates_issue_when_none_open() -> None:
    created: list[dict[str, str]] = []
    publisher = NoticePublisher(
        search_open=lambda **_kwargs: [],
        create_issue=lambda **kwargs: created.append(kwargs) or 9001,
        comment_issue=lambda *_args, **_kwargs: None,
    )
    number = publisher.publish(
        workflow="CI Standard",
        ref="refs/heads/main",
        run_id="12345",
        repository="D-sorganization/Runner_Dashboard",
        run_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/12345",
    )
    assert number == 9001
    assert len(created) == 1
    assert created[0]["labels"] == ["ci"]
    assert "actions/runs/12345" in created[0]["body"]


def test_publish_comments_existing_open_issue() -> None:
    comments: list[tuple[int, str]] = []
    publisher = NoticePublisher(
        search_open=lambda **_kwargs: [{"number": 42, "title": "[CI failure] CI Standard on refs/heads/main"}],
        create_issue=lambda **_kwargs: 0,
        comment_issue=lambda number, body: comments.append((number, body)),
    )
    number = publisher.publish(
        workflow="CI Standard",
        ref="refs/heads/main",
        run_id="999",
        repository="D-sorganization/Runner_Dashboard",
        run_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/999",
    )
    assert number == 42
    assert len(comments) == 1
    assert comments[0][0] == 42
    assert "runs/999" in comments[0][1]


def test_notice_body_contains_run_link() -> None:
    body = build_notice_body(
        workflow="CI Nightly (Integration)",
        ref="refs/heads/main",
        run_id="1",
        repository="D-sorganization/Runner_Dashboard",
        run_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/1",
    )
    assert "CI Nightly (Integration)" in body
    assert "actions/runs/1" in body
