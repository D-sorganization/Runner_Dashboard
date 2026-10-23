"""Bounded offline Git proof and run provenance for Cartographer citations."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

from knowledge_pack import KnowledgePack
from knowledge_pack.chunking import chunk_document
from knowledge_pack.manifest import PackManifest, manifest_from_dict
from knowledge_pack.pack import PackFormatError
from knowledge_pack.sources import matches
from library.citation_claims import claim_is_supported
from library.codebase_retrieval_ledger import codebase_references_for_run
from security import safe_subprocess_env

_REF = re.compile(
    r"\[ref:(?P<node>[A-Za-z0-9][A-Za-z0-9.-]{0,63}):codebase:"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})@(?P<commit>[a-f0-9]{8}):"
    r"(?P<path>[^\[\]#@\r\n]+)#L(?P<start>\d+)-L(?P<end>\d+)\]"
)
REFERENCE_TOKEN = re.compile(r"\[ref:[^\]\r\n]*(?:\]|$)", re.MULTILINE)
MAX_BLOB_BYTES = 1_000_000
MAX_SPAN_LINES = 400
_VERIFY_SECONDS = 10


def _git(root: Path, *args: str, deadline: float) -> bytes:
    timeout = min(3, deadline - time.monotonic())
    if timeout <= 0:
        raise TimeoutError("codebase verification deadline exceeded")
    env = safe_subprocess_env()
    for key in (
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_PARAMETERS",
    ):
        env.pop(key, None)
    env.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="", GIT_CONFIG_COUNT="0", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", "--no-replace-objects", "--literal-pathspecs", "-C", str(root), *args],
        capture_output=True,
        check=True,
        timeout=timeout,
        env=env,
    ).stdout


def _source(root: Path, fields: dict[str, str], deadline: float) -> tuple[str, str]:
    relative = fields["path"]
    if (
        PurePosixPath(relative).is_absolute()
        or "\\" in relative
        or any(p in ("", ".", "..") for p in relative.split("/"))
    ):
        raise ValueError("invalid codebase path")
    start, end = int(fields["start"]), int(fields["end"])
    if start < 1 or end < start or end - start >= MAX_SPAN_LINES:
        raise ValueError("invalid codebase range")
    commit = _git(root, "rev-parse", "--verify", f"{fields['commit']}^{{commit}}", deadline=deadline).decode().strip()
    tree = _git(root, "ls-tree", "-z", commit, "--", relative, deadline=deadline)
    metadata, path = tree.rstrip(b"\0").split(b"\t", 1)
    mode, kind, blob = metadata.split()
    if mode not in {b"100644", b"100755"} or kind != b"blob" or path.decode() != relative:
        raise ValueError("codebase path is not a regular blob")
    if int(_git(root, "cat-file", "-s", blob.decode(), deadline=deadline)) > MAX_BLOB_BYTES:
        raise ValueError("codebase blob exceeds limit")
    raw = _git(root, "cat-file", "blob", blob.decode(), deadline=deadline)
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if end > len(text.splitlines()):
        raise ValueError("codebase range exceeds source")
    return commit, text


def _context() -> tuple[dict[str, Path], PackManifest]:
    from staff.knowledge_refresh import get_knowledge_dir, resolve_pack_roots

    path = get_knowledge_dir() / "cartographer.sqlite"
    pack = KnowledgePack.open(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        manifest = manifest_from_dict(
            json.loads(conn.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()[0])
        )
    return resolve_pack_roots(pack) or {}, manifest


def _retrieved_text(fields: dict[str, str], text: str, refs: list[dict[str, Any]], chunk_chars: int) -> list[str]:
    hashes = {
        ref["content_hash"]
        for ref in refs
        if ref["node"] == fields["node"]
        and ref["repo"] == fields["repo"]
        and ref["commit_sha"] == fields["commit"]
        and ref["relative_path"] == fields["path"]
        and ref["line_start"] == int(fields["start"])
        and ref["line_end"] == int(fields["end"])
    }
    pieces = []
    for chunk in chunk_document(text, suffix=Path(fields["path"]).suffix, max_chars=chunk_chars):
        if hashlib.sha256(chunk.text.encode()).hexdigest() not in hashes:
            continue
        offset = text.find(chunk.text)
        start = text.count("\n", 0, offset) + 1
        if offset >= 0 and start == int(fields["start"]) and start + chunk.text.count("\n") == int(fields["end"]):
            pieces.append(chunk.text)
    return pieces


def verify_codebase_citations(db: Path, content: str, run_id: str | None) -> list[dict[str, Any]]:
    """Prove local immutable source and exact run scope; never return source text.

    Only indexed configured repositories are eligible. No pack rebuild, tree scan
    or Git transport is allowed. Historical chunks unavailable under the current
    chunking manifest fail closed rather than trusting their stored digest alone.
    """
    tokens = [m for m in REFERENCE_TOKEN.finditer(content) if ":codebase:" in m[0]]
    if not tokens:
        return []
    if len(tokens) > 100:
        return [{"verified": False, "status": "citation_limit_exceeded"}]
    try:
        deadline = time.monotonic() + _VERIFY_SECONDS
        roots, manifest = _context()
        refs = codebase_references_for_run(db, run_id) if run_id is not None else []
    except (OSError, ValueError, PackFormatError, sqlite3.Error, KeyError, TypeError, IndexError):
        return [{"citation": m[0][:500], "verified": False, "status": "codebase_unavailable"} for m in tokens]
    results = []
    for token in tokens:
        result: dict[str, Any] = {"citation": token[0][:500], "verified": False, "status": "codebase_unavailable"}
        try:
            match = _REF.fullmatch(token[0])
            if match is None:
                raise ValueError("invalid codebase citation")
            fields = match.groupdict()
            if not any(spec.repo == fields["repo"] and matches(spec, fields["path"]) for spec in manifest.sources):
                raise ValueError("source is outside the indexed manifest")
            commit, text = _source(roots[fields["repo"]], fields, deadline)
            fields["commit"] = commit
            evidence = ["\n".join(text.splitlines()[int(fields["start"]) - 1 : int(fields["end"])])]
            if run_id is not None:
                evidence = _retrieved_text(fields, text, refs, manifest.chunk_chars)
                if not evidence:
                    result["status"] = "unretrieved_reference"
                    results.append(result)
                    continue
            supported = any(claim_is_supported(content, token.start(), piece) for piece in evidence)
            result.update(verified=supported, status="verified" if supported else "misappropriated_reference")
        except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError):
            pass
        results.append(result)
    return results
