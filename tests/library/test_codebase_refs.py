"""Codebase verification must prove immutable source and exact retrieval scope."""

import subprocess
from pathlib import Path

import pytest
from knowledge_pack import KnowledgePack
from knowledge_pack.cartographer import build_cartographer_pack, format_codebase_reference
from library.codebase_retrieval_ledger import record_codebase_references
from library.db import init_library_db
from library.verify import verify_content

pytestmark = pytest.mark.unit


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    root = tmp_path / "Repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    source = root / "docs/architecture/contract.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Contract\nRotational power improves turbine efficiency.\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    directory = tmp_path / "knowledge"
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(directory))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path))
    monkeypatch.setattr("dashboard_config.HOSTNAME", "desk")
    build_cartographer_pack({"Repo": root}, directory / "cartographer.sqlite")
    hit = KnowledgePack.open(directory / "cartographer.sqlite").search("rotational")[0]
    cite = format_codebase_reference(hit, root)
    reference = dict(
        node="desk",
        repo="Repo",
        commit_sha=hit.commit,
        relative_path=hit.source,
        line_start=2,
        line_end=2,
        content_hash=hit.content_hash,
        citation=cite,
    )
    return init_library_db(tmp_path / "library.sqlite"), cite, reference, root


def test_current_codebase_citation_is_verified_without_returning_source(evidence: tuple) -> None:
    db, cite, reference, root = evidence
    (root / reference["relative_path"]).write_text("dirty replacement")
    result = verify_content(db, f'"Rotational power" {cite}')
    assert result["verified"] and result["citations"][0]["verified"]
    assert "Rotational power" not in str(result)


def test_claim_cannot_borrow_unrelated_or_invented_source(evidence: tuple) -> None:
    db, cite, _, _ = evidence
    for claim in (
        '"Fabricated quotation"',
        "Bananas orbit quantum oceans",
        "Rotational power does not improve turbine efficiency.",
        "Bananas improve turbine efficiency.",
    ):
        result = verify_content(db, f"{claim} {cite}")
        assert not result["verified"]
        assert result["citations"][0]["status"] == "misappropriated_reference"


def test_run_membership_matches_full_identity_and_preserves_ledger(evidence: tuple) -> None:
    db, cite, reference, _ = evidence
    assert not verify_content(db, cite, run_id="run_one")["verified"]
    record_codebase_references(db, run_id="run_one", references=[reference])
    assert verify_content(db, cite, run_id="run_one")["verified"]
    assert not verify_content(db, cite, run_id="run_two")["verified"]
    assert not verify_content(db, cite.replace("ref:desk:", "ref:other:"), run_id="run_one")["verified"]
    assert not verify_content(db, cite.replace("L2-L2", "L1-L2"), run_id="run_one")["verified"]
    assert verify_content(db, cite, run_id="run_one")["verified"]


def test_ledger_hash_is_checked_against_committed_chunk(evidence: tuple) -> None:
    db, cite, reference, _ = evidence
    reference["content_hash"] = "f" * 64
    record_codebase_references(db, run_id="run_one", references=[reference])
    assert not verify_content(db, cite, run_id="run_one")["verified"]


@pytest.mark.parametrize("change", [("Repo@", "Unknown@"), ("contract.md", "../contract.md"), ("L2-L2", "L2-L99")])
def test_unknown_repositories_paths_and_ranges_fail_closed(evidence: tuple, change: tuple) -> None:
    db, cite, _, _ = evidence
    assert not verify_content(db, cite.replace(*change))["verified"]


def test_unparsed_reference_never_counts_as_verified(evidence: tuple) -> None:
    db, _, _, _ = evidence
    for cite in ("[ref:codebase:Repo:missing]", "[ref:desk:codebase:malformed]", "[ref:desk:codebase:unterminated"):
        assert not verify_content(db, cite)["verified"]


def test_missing_pack_fails_closed_without_rebuilding(
    evidence: tuple, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db, cite, _, _ = evidence
    missing = tmp_path / "missing"
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(missing))
    assert not verify_content(db, cite)["verified"]
    assert not missing.exists()


def test_partial_clone_cannot_run_transport(evidence: tuple, tmp_path: Path) -> None:
    db, cite, reference, root = evidence
    marker = tmp_path / "transport-ran"
    helper = tmp_path / "transport"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    helper.chmod(0o700)
    git(root, "config", "extensions.partialClone", "evil")
    git(root, "config", "remote.evil.promisor", "true")
    git(root, "config", "remote.evil.url", f"ext::{helper}")
    git(root, "config", "protocol.ext.allow", "always")
    assert not verify_content(db, cite.replace(reference["commit_sha"][:8], "f" * 8))["verified"]
    assert not marker.exists()


def test_same_line_chunks_require_the_retrieved_hash(evidence: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    db, _, reference, root = evidence
    (root / reference["relative_path"]).write_text("# Contract\n" + "rotational " * 190 + "later-only evidence\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "long paragraph")
    from staff.knowledge_refresh import get_knowledge_dir

    pack_path = get_knowledge_dir() / "cartographer.sqlite"
    build_cartographer_pack({"Repo": root}, pack_path)
    hits = KnowledgePack.open(pack_path).search("rotational", k=10)
    first = next(hit for hit in hits if "later-only" not in hit.text)
    last = next(hit for hit in hits if "later-only" in hit.text)
    cite = format_codebase_reference(first, root)
    assert cite == format_codebase_reference(last, root)
    ref = {**reference, "commit_sha": first.commit, "citation": cite, "content_hash": first.content_hash}
    record_codebase_references(db, run_id="run_one", references=[ref])
    assert not verify_content(db, f'"later-only evidence" {cite}', run_id="run_one")["verified"]
    record_codebase_references(db, run_id="run_one", references=[{**ref, "content_hash": last.content_hash}])
    assert verify_content(db, f'"later-only evidence" {cite}', run_id="run_one")["verified"]


@pytest.mark.parametrize("kind", ["symlink", "binary", "large"])
def test_nonregular_or_unbounded_blobs_fail_closed(evidence: tuple, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    db, cite, reference, root = evidence
    path = root / "docs/architecture/other.md"
    if kind == "symlink":
        path.symlink_to("contract.md")
    else:
        path.write_bytes(b"\xff\xfe" if kind == "binary" else b"x" * 50)
        monkeypatch.setattr("library.codebase_refs.MAX_BLOB_BYTES", 10 if kind == "large" else 1000)
    git(root, "add", ".")
    git(root, "commit", "-qm", kind)
    cite = cite.replace(reference["commit_sha"][:8], git(root, "rev-parse", "HEAD")[:8]).replace(
        "contract.md", "other.md"
    )
    assert not verify_content(db, cite)["verified"]


def test_verification_deadline_prevents_starting_git(evidence: tuple) -> None:
    from library.codebase_refs import _git

    with pytest.raises(TimeoutError):
        _git(evidence[3], "status", deadline=-1)


def test_reference_limit_is_fail_closed(evidence: tuple) -> None:
    db, cite, _, _ = evidence
    result = verify_content(db, " ".join([cite] * 101))
    assert not result["verified"] and result["citations"][0]["status"] == "citation_limit_exceeded"


def test_corrupt_pack_is_unavailable_without_crashing(evidence: tuple) -> None:
    from staff.knowledge_refresh import get_knowledge_dir

    db, cite, _, _ = evidence
    (get_knowledge_dir() / "cartographer.sqlite").write_bytes(b"not a SQLite database")
    result = verify_content(db, cite)
    assert not result["verified"] and result["citations"][0]["status"] == "codebase_unavailable"


def test_explicit_knowledge_directory_encodes_uri_special_characters(
    evidence: tuple, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db, cite, _, root = evidence
    selected = tmp_path / "knowledge#?selected"
    build_cartographer_pack({"Repo": root}, selected / "cartographer.sqlite")
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(tmp_path / "ambient-must-not-be-used"))

    result = verify_content(
        db,
        f'"Rotational power" {cite}',
        knowledge_dir=selected,
        allow_ambient_knowledge=False,
    )

    assert result["verified"] is True
