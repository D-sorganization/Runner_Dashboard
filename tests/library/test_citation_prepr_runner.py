from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from library.db import init_library_db

from scripts.verify_citation_inputs import verify_inputs


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _repo(root: Path, files: dict[str, str]) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-qm", "initial")
    return _git(root, "rev-parse", "HEAD")


def test_zero_citation_documents_and_pr_body_check_links_without_database(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"docs/guide.md": "# Start Here\n", "docs/nested/topic.md": "No citations.\n"})
    document = root / "docs" / "nested" / "topic.md"
    document.write_text("See [guide](../guide.md#start-here).\n", encoding="utf-8")
    body = tmp_path / "pr-body.md"
    body.write_text("See [guide](docs/guide.md#start-here).\n", encoding="utf-8")

    result = verify_inputs(root, base=base, pr_body_file=body)

    assert result["verified"] is True
    assert [item["path"] for item in result["documents"]] == ["docs/nested/topic.md", "PR body"]
    assert all(item["citation_statuses"] == [] for item in result["documents"])
    assert all(item["link_statuses"] == ["verified"] for item in result["documents"])


def test_citation_requires_explicit_database_and_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"claim.md": "No citations.\n"})
    (root / "claim.md").write_text("A claim [ref:desk:shared:w1:p2#sha256=abcdef12:p.1-1]\n", encoding="utf-8")

    result = verify_inputs(root, base=base)

    assert result["verified"] is False
    assert result["documents"][0]["citation_statuses"] == ["library_database_required"]


def test_explicit_database_verifies_local_citation_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"claim.md": "Initial text.\n"})
    source = root / "paper.md"
    text = "Rotational power improves turbine efficiency."
    source.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    database = init_library_db(tmp_path / "evidence" / "library.sqlite")
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO works(id,sha256,author,title,format) VALUES(1,?,?,?,?)",
            (digest, "Doe", "Turbine", "md"),
        )
        connection.execute(
            "INSERT INTO files(id,work_id,source_id,rel_path,mtime,size,sha256,source_root) "
            "VALUES(1,1,'shared','paper.md',0,1,?,?)",
            (digest, str(tmp_path)),
        )
        connection.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,section,text,token_estimate) "
            "VALUES(2,1,1,1,'Power',?,6)",
            (text,),
        )
    document = root / "claim.md"
    document.write_text(f"{text} [ref:desk:shared:w1:p2#sha256={digest[:8]}:p.1-1]\n", encoding="utf-8")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "desk", raising=False)

    result = verify_inputs(root, base=base, library_db=database)

    assert result["verified"] is True
    assert result["documents"][0]["citation_statuses"] == ["verified"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    document.write_text(f"{text} [ref:desk:shared:w1:p2#sha256=00000000:p.1-1]\n", encoding="utf-8")
    tampered = verify_inputs(root, base=base, library_db=database)
    assert tampered["verified"] is False
    assert tampered["documents"][0]["citation_statuses"] == ["sha_mismatch"]


def test_renames_verify_target_and_deletions_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"old.md": "[guide](guide.md)\n", "delete.md": "Gone.\n", "guide.md": "# Guide\n"})
    _git(root, "mv", "old.md", "renamed.md")
    (root / "delete.md").unlink()

    result = verify_inputs(root, base=base)

    assert result["verified"] is True
    assert [item["path"] for item in result["documents"]] == ["renamed.md"]


def test_changed_symlink_outside_repository_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"README.md": "Initial.\n"})
    outside = tmp_path / "private.md"
    outside.write_text("No access.\n", encoding="utf-8")
    try:
        (root / "external.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    _git(root, "add", "external.md")

    with pytest.raises(ValueError, match="inside repository root"):
        verify_inputs(root, base=base)


def test_library_verification_never_attempts_peer_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"claim.md": "Initial.\n"})
    document = root / "claim.md"
    document.write_text("A claim [ref:peer:shared:w1:p2#sha256=abcdef12:p.1-1]\n", encoding="utf-8")
    database = init_library_db(tmp_path / "library.sqlite")
    import dashboard_config
    from library import verify

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "desk", raising=False)
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"peer": "https://peer.invalid"}, raising=False)
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-token")
    monkeypatch.setattr(verify, "_fetch_peer_response", lambda *args, **kwargs: pytest.fail("network attempted"))

    result = verify_inputs(root, base=base, library_db=database)

    assert result["verified"] is False
    assert result["documents"][0]["citation_statuses"] == ["node_mismatch"]


def test_zero_citation_broken_link_fails_without_database(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"broken.md": "Initial.\n"})
    (root / "broken.md").write_text("See [missing](missing.md#start).\n", encoding="utf-8")

    result = verify_inputs(root, base=base)

    assert result["verified"] is False
    assert result["documents"][0]["link_statuses"] == ["broken_link"]


def test_codebase_citation_disables_ambient_pack_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"claim.md": "Initial.\n"})
    (root / "claim.md").write_text("Claim [ref:desk:codebase:Repo@12345678:src/module.py#L1-L1]\n", encoding="utf-8")
    database = init_library_db(tmp_path / "library.sqlite")
    ambient = tmp_path / "ambient-pack"
    ambient.mkdir()
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(ambient))
    from library import codebase_refs

    calls: list[tuple[Path | None, bool]] = []

    def unavailable(knowledge_dir: Path | None = None, *, allow_ambient: bool = True):
        calls.append((knowledge_dir, allow_ambient))
        raise FileNotFoundError("explicit pack missing")

    monkeypatch.setattr(codebase_refs, "_context", unavailable)

    result = verify_inputs(root, base=base, library_db=database)

    assert result["verified"] is False
    assert result["documents"][0]["citation_statuses"] == ["codebase_unavailable"]
    assert calls == [(None, False)]


def test_changed_markdown_file_size_limit_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"guide.md": "Initial.\n"})
    (root / "guide.md").write_text("x" * 20, encoding="utf-8")
    from scripts import verify_citation_inputs

    monkeypatch.setattr(verify_citation_inputs, "_MAX_FILE_BYTES", 10)

    with pytest.raises(ValueError, match="size limit"):
        verify_inputs(root, base=base)


def test_aggregate_size_limit_covers_markdown_and_pr_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"guide.md": "Initial.\n"})
    (root / "guide.md").write_text("small\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text("x" * 20, encoding="utf-8")
    from scripts import verify_citation_inputs

    monkeypatch.setattr(verify_citation_inputs, "_MAX_FILE_BYTES", 100)
    monkeypatch.setattr(verify_citation_inputs, "_MAX_TOTAL_BYTES", 10)

    with pytest.raises(ValueError, match="size limit"):
        verify_inputs(root, base=base, pr_body_file=body)


def test_changed_markdown_file_count_limit_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    base = _repo(root, {"one.md": "One.\n", "two.md": "Two.\n"})
    (root / "one.md").write_text("Changed one.\n", encoding="utf-8")
    (root / "two.md").write_text("Changed two.\n", encoding="utf-8")
    from scripts import verify_citation_inputs

    monkeypatch.setattr(verify_citation_inputs, "_MAX_FILES", 1)

    with pytest.raises(ValueError, match="file count"):
        verify_inputs(root, base=base)
