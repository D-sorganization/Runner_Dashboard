"""Unit tests for ``read_issue`` chat context injection (Runner_Dashboard#1762)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from staff.chat_issue_context import (
    MAX_BLOCK_CHARS,
    MAX_BODY_CHARS,
    MAX_MD_FILE_CHARS,
    IssueFetcher,
    IssueRef,
    build_issue_context_block,
    build_referenced_items_block,
    parse_issue_refs,
    role_declares_read_issue,
)
from staff.roles import RoleSpec

# ── parse_issue_refs ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_owner_repo_hash_form() -> None:
    refs = parse_issue_refs("Please look at D-sorganization/UpstreamDrift#11080 today.")
    assert refs == [IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=11080)]


@pytest.mark.unit
def test_parse_github_url_form() -> None:
    refs = parse_issue_refs("See https://github.com/D-sorganization/UpstreamDrift/pull/11080 for the diff.")
    assert refs == [IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=11080)]

    issue_refs = parse_issue_refs("See https://github.com/D-sorganization/Runner_Dashboard/issues/42 please.")
    assert issue_refs == [IssueRef(owner="D-sorganization", repo="Runner_Dashboard", number=42)]


@pytest.mark.unit
def test_parse_bare_number_requires_known_repo_name_in_message() -> None:
    refs = parse_issue_refs("Take UpstreamDrift PR #11080 to the Board.")
    assert refs == [IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=11080)]

    # No known repo name anywhere in the message -> bare '#N' is not resolved.
    assert parse_issue_refs("Take #11080 to the Board.") == []


@pytest.mark.unit
def test_parse_bare_issue_and_hash_only_forms() -> None:
    assert parse_issue_refs("Runner_Dashboard issue #42 needs a look.") == [
        IssueRef(owner="D-sorganization", repo="Runner_Dashboard", number=42)
    ]
    assert parse_issue_refs("Runner_Dashboard #42 needs a look.") == [
        IssueRef(owner="D-sorganization", repo="Runner_Dashboard", number=42)
    ]


@pytest.mark.unit
def test_parse_uses_role_repos_over_default_names() -> None:
    refs = parse_issue_refs("Sprocket #7 is blocking.", repo_names=("Sprocket",))
    assert refs == [IssueRef(owner="D-sorganization", repo="Sprocket", number=7)]
    # 'Sprocket' is not in the default fallback tuple, so without repo_names it is not resolved.
    assert parse_issue_refs("Sprocket #7 is blocking.") == []


@pytest.mark.unit
def test_parse_dedupes_and_caps_at_three() -> None:
    text = (
        "D-sorganization/UpstreamDrift#1 and D-sorganization/UpstreamDrift#1 again, "
        "plus D-sorganization/UpstreamDrift#2, D-sorganization/UpstreamDrift#3, "
        "D-sorganization/UpstreamDrift#4."
    )
    refs = parse_issue_refs(text)
    assert refs == [
        IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=1),
        IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=2),
        IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=3),
    ]


@pytest.mark.unit
def test_parse_repo_hash_form_without_owner() -> None:
    """``Repo#N`` names its own repo; it is not a bare ref for another repo (#1781)."""
    text = (
        "Runner_Dashboard PR #1776 proposes 18 drafts, filed as Board proposals "
        "Repository_Management#1848 through #1865 in the same order."
    )
    assert parse_issue_refs(text) == [
        IssueRef(owner="D-sorganization", repo="Runner_Dashboard", number=1776),
        IssueRef(owner="D-sorganization", repo="Repository_Management", number=1848),
        IssueRef(owner="D-sorganization", repo="Repository_Management", number=1865),
    ]


@pytest.mark.unit
def test_parse_bare_number_binds_to_nearest_preceding_repo() -> None:
    assert parse_issue_refs("UpstreamDrift PR #5 and Tools #7 need review.") == [
        IssueRef(owner="D-sorganization", repo="UpstreamDrift", number=5),
        IssueRef(owner="D-sorganization", repo="Tools", number=7),
    ]
    # A bare ref before any repo mention still falls back to the first repo named.
    assert parse_issue_refs("#42 is the Runner_Dashboard bug.") == [
        IssueRef(owner="D-sorganization", repo="Runner_Dashboard", number=42)
    ]


@pytest.mark.unit
def test_parse_no_refs_in_plain_message() -> None:
    assert parse_issue_refs("Good morning, how is the fleet?") == []


# ── role_declares_read_issue ─────────────────────────────────────────────────


@pytest.mark.unit
def test_role_declares_read_issue() -> None:
    role = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_issue", "read_run"]})
    assert role_declares_read_issue(role) is True

    role_without = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_run"]})
    assert role_declares_read_issue(role_without) is False

    assert role_declares_read_issue(None) is False


# ── build_issue_context_block ────────────────────────────────────────────────


def _role(*, tools: tuple[str, ...] = ("read_issue",), repos: tuple[str, ...] = ()) -> RoleSpec:
    return RoleSpec(name="barb", title="Barb", chat={"tools": list(tools)}, repos=repos)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_none_without_read_issue_tool() -> None:
    role = _role(tools=("read_run",))
    fetch = IssueFetcher(gh_api=AsyncMock(), gh_api_raw=AsyncMock())
    block = await build_issue_context_block(role, "D-sorganization/UpstreamDrift#11080", fetch=fetch)
    assert block is None
    fetch.gh_api.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_none_without_a_reference() -> None:
    role = _role()
    fetch = IssueFetcher(gh_api=AsyncMock(), gh_api_raw=AsyncMock())
    block = await build_issue_context_block(role, "Good morning!", fetch=fetch)
    assert block is None
    fetch.gh_api.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_renders_issue_fields() -> None:
    role = _role()

    async def fake_gh_api(endpoint: str) -> dict[str, Any]:
        assert endpoint == "repos/D-sorganization/UpstreamDrift/issues/11080"
        return {
            "title": "Proposal packet",
            "state": "open",
            "labels": [{"name": "panel-review"}, {"name": "judgement:contested"}],
            "body": "The review found a mirrored delivery frame.",
        }

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=AsyncMock())
    block = await build_issue_context_block(role, "D-sorganization/UpstreamDrift#11080", fetch=fetch)

    assert block is not None
    assert block.startswith("## Referenced items")
    assert "D-sorganization/UpstreamDrift#11080" in block
    assert "Proposal packet" in block
    assert "(open)" in block
    assert "panel-review, judgement:contested" in block
    assert "mirrored delivery frame" in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_renders_pr_files_and_markdown() -> None:
    role = _role()

    async def fake_gh_api(endpoint: str) -> Any:
        if endpoint == "repos/D-sorganization/UpstreamDrift/issues/11080":
            return {"title": "Bounce fix", "state": "open", "labels": [], "body": "b", "pull_request": {}}
        if endpoint == "repos/D-sorganization/UpstreamDrift/pulls/11080":
            return {"head": {"sha": "deadbeef"}}
        if endpoint == "repos/D-sorganization/UpstreamDrift/pulls/11080/files":
            return [{"filename": "docs/README.md"}, {"filename": "src/bounce.py"}]
        raise AssertionError(f"unexpected endpoint {endpoint}")

    async def fake_gh_api_raw(endpoint: str) -> str:
        assert endpoint == "repos/D-sorganization/UpstreamDrift/contents/docs/README.md?ref=deadbeef"
        return "# Bounce fix notes"

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=fake_gh_api_raw)
    block = await build_issue_context_block(role, "D-sorganization/UpstreamDrift#11080", fetch=fetch)

    assert block is not None
    assert "docs/README.md" in block
    assert "src/bounce.py" in block
    assert "# Bounce fix notes" in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_failed_fetch_renders_unavailable_and_never_raises() -> None:
    role = _role()

    async def failing_gh_api(endpoint: str) -> dict[str, Any]:
        raise RuntimeError("secret /home/x path")

    fetch = IssueFetcher(gh_api=failing_gh_api, gh_api_raw=AsyncMock())
    block = await build_issue_context_block(role, "D-sorganization/UpstreamDrift#11080", fetch=fetch)

    assert block is not None
    assert "unavailable (RuntimeError)" in block
    assert "secret /home/x path" not in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_caps_body_and_md_file_and_total() -> None:
    role = _role()

    async def fake_gh_api(endpoint: str) -> Any:
        if endpoint.endswith("/issues/11080"):
            return {
                "title": "t",
                "state": "open",
                "labels": [],
                "body": "X" * (MAX_BODY_CHARS + 500),
                "pull_request": {},
            }
        if endpoint.endswith("/pulls/11080"):
            return {"head": {"sha": "sha1"}}
        if endpoint.endswith("/pulls/11080/files"):
            return [{"filename": "BIG.md"}]
        raise AssertionError(endpoint)

    async def fake_gh_api_raw(endpoint: str) -> str:
        return "Y" * (MAX_MD_FILE_CHARS + 500)

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=fake_gh_api_raw)
    block = await build_issue_context_block(role, "D-sorganization/UpstreamDrift#11080", fetch=fetch)

    assert block is not None
    assert len(block) <= MAX_BLOCK_CHARS
    assert "…(truncated)" in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_issue_context_block_max_three_refs() -> None:
    role = _role()
    calls: list[str] = []

    async def fake_gh_api(endpoint: str) -> dict[str, Any]:
        calls.append(endpoint)
        return {"title": "t", "state": "open", "labels": [], "body": ""}

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=AsyncMock())
    text = (
        "D-sorganization/UpstreamDrift#1, D-sorganization/UpstreamDrift#2, "
        "D-sorganization/UpstreamDrift#3, D-sorganization/UpstreamDrift#4"
    )
    block = await build_issue_context_block(role, text, fetch=fetch)
    assert block is not None
    assert len(calls) == 3


# ── build_referenced_items_block (role-independent, Runner_Dashboard#1767) ──────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_referenced_items_block_needs_no_role() -> None:
    """Unlike build_issue_context_block, this is usable outside a role's read_issue chat tool."""

    async def fake_gh_api(endpoint: str) -> dict[str, Any]:
        return {"title": "Proposal packet", "state": "open", "labels": [], "body": "body text"}

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=AsyncMock())
    block = await build_referenced_items_block("D-sorganization/UpstreamDrift#11080", fetch=fetch)

    assert block is not None
    assert block.startswith("## Referenced items")
    assert "Proposal packet" in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_referenced_items_block_none_without_a_reference() -> None:
    fetch = IssueFetcher(gh_api=AsyncMock(), gh_api_raw=AsyncMock())
    block = await build_referenced_items_block("Good morning!", fetch=fetch)
    assert block is None
    fetch.gh_api.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_referenced_items_block_custom_md_and_block_chars() -> None:
    """Board turns pass larger md_chars/block_chars than a chat turn's module defaults."""

    async def fake_gh_api(endpoint: str) -> Any:
        if endpoint.endswith("/issues/11080"):
            return {"title": "t", "state": "open", "labels": [], "body": "b", "pull_request": {}}
        if endpoint.endswith("/pulls/11080"):
            return {"head": {"sha": "sha1"}}
        if endpoint.endswith("/pulls/11080/files"):
            return [{"filename": "BIG.md"}]
        raise AssertionError(endpoint)

    async def fake_gh_api_raw(endpoint: str) -> str:
        # Bigger than the default MAX_MD_FILE_CHARS but under the custom md_chars budget.
        return "Y" * (MAX_MD_FILE_CHARS + 500)

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=fake_gh_api_raw)
    block = await build_referenced_items_block(
        "D-sorganization/UpstreamDrift#11080",
        md_chars=MAX_MD_FILE_CHARS + 1000,
        block_chars=200_000,
        fetch=fetch,
    )

    assert block is not None
    assert "…(truncated)" not in block


# ── Omitted headings after truncation (Runner_Dashboard#1767) ───────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_truncated_markdown_lists_omitted_headings() -> None:
    body = "A" * 100 + "\n# First Cut Heading\nsome text\n## Second Cut Heading\nmore text\n"
    md_chars = 100  # Cuts before both headings.

    async def fake_gh_api(endpoint: str) -> Any:
        if endpoint.endswith("/issues/11080"):
            return {"title": "t", "state": "open", "labels": [], "body": "b", "pull_request": {}}
        if endpoint.endswith("/pulls/11080"):
            return {"head": {"sha": "sha1"}}
        if endpoint.endswith("/pulls/11080/files"):
            return [{"filename": "NOTES.md"}]
        raise AssertionError(endpoint)

    async def fake_gh_api_raw(endpoint: str) -> str:
        return body

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=fake_gh_api_raw)
    block = await build_referenced_items_block(
        "D-sorganization/UpstreamDrift#11080",
        md_chars=md_chars,
        fetch=fetch,
    )

    assert block is not None
    assert "…(truncated)" in block
    assert "Omitted headings: # First Cut Heading | ## Second Cut Heading" in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_truncated_markdown_with_no_trailing_headings_has_no_omitted_line() -> None:
    body = "A" * 200  # No headings at all.
    md_chars = 100

    async def fake_gh_api(endpoint: str) -> Any:
        if endpoint.endswith("/issues/11080"):
            return {"title": "t", "state": "open", "labels": [], "body": "b", "pull_request": {}}
        if endpoint.endswith("/pulls/11080"):
            return {"head": {"sha": "sha1"}}
        if endpoint.endswith("/pulls/11080/files"):
            return [{"filename": "NOTES.md"}]
        raise AssertionError(endpoint)

    async def fake_gh_api_raw(endpoint: str) -> str:
        return body

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=fake_gh_api_raw)
    block = await build_referenced_items_block(
        "D-sorganization/UpstreamDrift#11080",
        md_chars=md_chars,
        fetch=fetch,
    )

    assert block is not None
    assert "…(truncated)" in block
    assert "Omitted headings" not in block
