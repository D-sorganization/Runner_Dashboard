from __future__ import annotations

from pathlib import Path

import pytest
from library.db import init_library_db
from library.markdown_links import verify_links
from library.verify import verify_content


def test_link_only_markdown_is_verified_without_a_database(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "docs" / "nested").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Start Here\n", encoding="utf-8")
    source = root / "docs" / "nested" / "topic.md"
    source.write_text("[guide](../guide.md#start-here)", encoding="utf-8")

    links = verify_links(
        "[guide](../guide.md#start-here)",
        repo_root=root,
        document_path=source,
    )

    assert links == [{"url": "../guide.md#start-here", "verified": True, "status": "verified"}]


def test_pr_body_links_remain_repository_root_relative(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "guide.md").write_text("# Start Here\n", encoding="utf-8")

    assert verify_links("[guide](guide.md#start-here)", repo_root=root) == [
        {"url": "guide.md#start-here", "verified": True, "status": "verified"}
    ]


def test_same_document_anchor_is_supported(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    document = root / "guide.md"
    document.write_text("# Start Here\n", encoding="utf-8")

    assert verify_links("[here](#start-here)", repo_root=root, document_path=document) == [
        {"url": "#start-here", "verified": True, "status": "verified"}
    ]


def test_verify_content_uses_document_relative_links(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / "docs" / "nested" / "topic.md"
    target = root / "docs" / "guide.md"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("[guide](../guide.md#start-here)", encoding="utf-8")
    target.write_text("# Start Here\n", encoding="utf-8")
    db = init_library_db(tmp_path / "library.sqlite")

    result = verify_content(
        db,
        source.read_text(encoding="utf-8"),
        repo_root=root,
        document_path=source,
    )

    assert result["verified"] is True
    assert result["links"] == [{"url": "../guide.md#start-here", "verified": True, "status": "verified"}]


@pytest.mark.parametrize("target", ["../../outside.md", "missing.md", "guide.md#absent"])
def test_invalid_relative_links_fail_closed(tmp_path: Path, target: str) -> None:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("[target]", encoding="utf-8")
    (root / "guide.md").write_text("# Existing\n", encoding="utf-8")
    (tmp_path / "outside.md").write_text("# Outside\n", encoding="utf-8")

    result = verify_links(f"[target]({target})", repo_root=root, document_path=root / "docs" / "a.md")

    assert result == [{"url": target, "verified": False, "status": "broken_link"}]


def test_document_path_must_be_inside_repository_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("[guide](guide.md)", encoding="utf-8")

    with pytest.raises(ValueError, match="inside repository root"):
        verify_links(outside.read_text(encoding="utf-8"), repo_root=root, document_path=outside)


def test_document_symlink_cannot_escape_repository_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("[guide](guide.md)", encoding="utf-8")
    linked = root / "linked.md"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="inside repository root"):
        verify_links(outside.read_text(encoding="utf-8"), repo_root=root, document_path=linked)


def test_symlink_link_target_cannot_escape_repository_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    try:
        (root / "linked.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    assert verify_links("[outside](linked.md#outside)", repo_root=root)[0]["verified"] is False
