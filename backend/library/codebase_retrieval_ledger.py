"""Append-only typed run references for committed Cartographer passages."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,63}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_COMMIT = re.compile(r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
_HASH = re.compile(r"^[a-fA-F0-9]{64}$")
_FIELDS = frozenset(
    {"node", "repo", "commit_sha", "relative_path", "line_start", "line_end", "content_hash", "citation"}
)
_MAX_REFERENCES_PER_CALL = 100
_MAX_REFERENCES_PER_RUN = 500

_SCHEMA = """CREATE TABLE IF NOT EXISTS staff_run_codebase_references (
    run_id TEXT NOT NULL,
    node TEXT NOT NULL,
    repo TEXT NOT NULL,
    commit_sha TEXT NOT NULL CHECK(length(commit_sha) IN (40,64)),
    relative_path TEXT NOT NULL,
    line_start INTEGER NOT NULL CHECK(line_start > 0),
    line_end INTEGER NOT NULL CHECK(line_end >= line_start),
    content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
    citation TEXT NOT NULL,
    retrieved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(run_id,node,repo,commit_sha,relative_path,line_start,line_end,content_hash)
)"""


def _canonical_reference(reference: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(reference, dict) or set(reference) != _FIELDS:
        raise ValueError("reference must contain only the canonical codebase identity fields")
    node, repo = reference["node"], reference["repo"]
    if not isinstance(node, str) or not _NODE.fullmatch(node):
        raise ValueError("invalid reference node")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise ValueError("invalid reference repository")
    commit = reference["commit_sha"]
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ValueError("commit_sha must be a full Git commit SHA")
    relative_path = reference["relative_path"]
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or relative_path.startswith(("/", "\\"))
        or "\\" in relative_path
        or "\x00" in relative_path
        or re.match(r"^[A-Za-z]:", relative_path) is not None
        or any(ord(character) < 32 or ord(character) == 127 for character in relative_path)
        or any(part in ("", ".", "..") for part in relative_path.split("/"))
    ):
        raise ValueError("relative_path must be a normalized repository-relative path")
    lines: dict[str, int] = {}
    for key in ("line_start", "line_end"):
        value = reference[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        lines[key] = value
    if lines["line_end"] < lines["line_start"]:
        raise ValueError("line_end must be greater than or equal to line_start")
    content_hash = reference["content_hash"]
    if not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash):
        raise ValueError("content_hash must be a full SHA-256 digest")
    commit = commit.lower()
    content_hash = content_hash.lower()
    citation = reference["citation"]
    expected_citation = (
        f"[ref:{node}:codebase:{repo}@{commit[:8]}:{relative_path}#L{lines['line_start']}-L{lines['line_end']}]"
    )
    if not isinstance(citation, str) or citation != expected_citation:
        raise ValueError("citation does not match the canonical codebase identity")
    return {
        "node": node,
        "repo": repo,
        "commit_sha": commit,
        "relative_path": relative_path,
        **lines,
        "content_hash": content_hash,
        "citation": expected_citation,
    }


def record_codebase_references(db: Path, *, run_id: str, references: list[dict[str, Any]]) -> int:
    """Append up to 100 validated codebase identities, deduplicated within their run."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if not isinstance(references, list) or len(references) > _MAX_REFERENCES_PER_CALL:
        raise ValueError("at most 100 codebase references may be recorded")
    canonical = [_canonical_reference(reference) for reference in references]
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_SCHEMA)
        existing = conn.execute(
            "SELECT count(*) FROM staff_run_codebase_references WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        known = {
            (
                row["node"],
                row["repo"],
                row["commit_sha"],
                row["relative_path"],
                row["line_start"],
                row["line_end"],
                row["content_hash"],
            )
            for row in conn.execute(
                "SELECT node,repo,commit_sha,relative_path,line_start,line_end,content_hash "
                "FROM staff_run_codebase_references WHERE run_id=?",
                (run_id,),
            )
        }
        identities = {
            (
                row["node"],
                row["repo"],
                row["commit_sha"],
                row["relative_path"],
                row["line_start"],
                row["line_end"],
                row["content_hash"],
            )
            for row in canonical
        }
        if existing + len(identities - known) > _MAX_REFERENCES_PER_RUN:
            raise ValueError("run codebase reference ledger exceeds 500 references")
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO staff_run_codebase_references "
            "(run_id,node,repo,commit_sha,relative_path,line_start,line_end,content_hash,citation) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_id,
                    row["node"],
                    row["repo"],
                    row["commit_sha"],
                    row["relative_path"],
                    row["line_start"],
                    row["line_end"],
                    row["content_hash"],
                    row["citation"],
                )
                for row in canonical
            ],
        )
        conn.commit()
        return conn.total_changes - before


def codebase_references_for_run(db: Path, run_id: str) -> list[dict[str, Any]]:
    """Return the stable, text-free codebase identities recorded for one run."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        rows = conn.execute(
            "SELECT run_id,node,repo,commit_sha,relative_path,line_start,line_end,content_hash,citation "
            "FROM staff_run_codebase_references WHERE run_id=? "
            "ORDER BY node,repo,commit_sha,relative_path,line_start,line_end,content_hash",
            (run_id,),
        ).fetchall()
    keys = (
        "run_id",
        "node",
        "repo",
        "commit_sha",
        "relative_path",
        "line_start",
        "line_end",
        "content_hash",
        "citation",
    )
    return [cast(dict[str, Any], dict(zip(keys, row, strict=True))) for row in rows]
