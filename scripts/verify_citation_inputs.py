#!/usr/bin/env python3
"""Locally verify citation and Markdown links in changed tracked documents."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from library.markdown_links import verify_links  # noqa: E402
from library.verify import verify_content  # noqa: E402
from security import safe_subprocess_env  # noqa: E402

_CITATION_MARKER = re.compile(r"\[ref:|lib://|\(work_id=")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_BASE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_MAX_FILES = 200
_MAX_FILE_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 10_000_000
_MAX_DIFF_BYTES = 2_000_000
_GIT_TIMEOUT_S = 5


def _git_environment() -> dict[str, str]:
    env = safe_subprocess_env()
    for key in tuple(env):
        if key in {
            "GIT_DIR",
            "GIT_COMMON_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_PARAMETERS",
        } or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            env.pop(key, None)
    env.update(
        GIT_ALLOW_PROTOCOL="",
        GIT_CONFIG_COUNT="0",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def _changed_markdown_paths(root: Path, base: str) -> list[Path]:
    if not _BASE_REF.fullmatch(base):
        raise ValueError("base must be a local Git ref name or object id")
    command = [
        "git",
        "--no-pager",
        "--no-replace-objects",
        "--no-optional-locks",
        "-C",
        str(root),
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "--no-ext-diff",
        "--no-textconv",
        base,
        "--",
        "*.md",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=True,
            timeout=_GIT_TIMEOUT_S,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("local Git diff failed or exceeded its deadline") from exc
    if len(completed.stdout) > _MAX_DIFF_BYTES:
        raise ValueError("changed-file list exceeds the verification limit")
    records = completed.stdout.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    paths: list[Path] = []
    index = 0
    while index < len(records):
        try:
            status = records[index].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Git returned an invalid file status") from exc
        index += 1
        count = 2 if status.startswith(("R", "C")) else 1
        if index + count > len(records):
            raise ValueError("Git returned an incomplete changed-file list")
        names = records[index : index + count]
        index += count
        if status == "D":
            continue
        raw_path = names[-1]
        try:
            relative = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("changed Markdown path is not valid UTF-8") from exc
        if not relative.lower().endswith(".md"):
            continue
        posix_path = PurePosixPath(relative)
        if posix_path.is_absolute() or "\\" in relative or any(part in ("", ".", "..") for part in relative.split("/")):
            raise ValueError("changed Markdown path is not repository-relative")
        try:
            path = (root / Path(*posix_path.parts)).resolve(strict=True)
        except OSError as exc:
            raise ValueError("changed Markdown document is unavailable") from exc
        if root not in path.parents or not path.is_file():
            raise ValueError("changed Markdown document must be a file inside repository root")
        paths.append(path)
        if len(paths) > _MAX_FILES:
            raise ValueError("changed Markdown file count exceeds the verification limit")
    return sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())


def _read_bounded(path: Path, remaining_bytes: int) -> tuple[str, int]:
    raw = path.read_bytes()
    if len(raw) > _MAX_FILE_BYTES or len(raw) > remaining_bytes:
        raise ValueError("verification input exceeds its size limit")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("verification input must be UTF-8 text") from exc
    if len(content) > _MAX_FILE_BYTES:
        raise ValueError("verification input exceeds its size limit")
    return content, len(raw)


def _verify_text(
    content: str,
    *,
    label: str,
    root: Path,
    document_path: Path | None,
    library_db: Path | None,
    knowledge_dir: Path | None,
    run_id: str | None,
) -> dict[str, Any]:
    if not _CITATION_MARKER.search(content):
        links = verify_links(content, repo_root=root, document_path=document_path)
        return {
            "path": label,
            "verified": all(item["verified"] for item in links),
            "citation_statuses": [],
            "link_statuses": [item["status"] for item in links],
        }
    if library_db is None:
        links = verify_links(content, repo_root=root, document_path=document_path)
        return {
            "path": label,
            "verified": False,
            "citation_statuses": ["library_database_required"],
            "link_statuses": [item["status"] for item in links],
        }
    result = verify_content(
        library_db,
        content,
        run_id=run_id,
        repo_root=root,
        document_path=document_path,
        knowledge_dir=knowledge_dir,
        allow_ambient_knowledge=False,
        local_only=True,
    )
    citations = result.get("citations", [])
    statuses = [item.get("status", "unverified") for item in citations if isinstance(item, dict)]
    marker_count = len(list(_CITATION_MARKER.finditer(content)))
    if len(statuses) < marker_count:
        statuses.extend(["invalid_citation"] * (marker_count - len(statuses)))
    links = result.get("links", [])
    link_statuses = [item.get("status", "broken_link") for item in links if isinstance(item, dict)]
    return {
        "path": label,
        "verified": result.get("verified") is True and len(statuses) >= marker_count,
        "citation_statuses": statuses,
        "link_statuses": link_statuses,
    }


def verify_inputs(
    repository_root: Path,
    *,
    base: str,
    pr_body_file: Path | None = None,
    library_db: Path | None = None,
    knowledge_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Verify changed Markdown and optional body using only explicit local evidence."""
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise ValueError("repository root is unavailable") from exc
    if not root.is_dir():
        raise ValueError("repository root must be a directory")
    if run_id is not None and not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    if library_db is not None and not Path(library_db).is_file():
        raise ValueError("explicit library database does not exist")
    if knowledge_dir is not None and not Path(knowledge_dir).is_dir():
        raise ValueError("explicit knowledge directory does not exist")

    changed = _changed_markdown_paths(root, base)
    results: list[dict[str, Any]] = []
    consumed = 0
    inputs: list[tuple[str, Path, Path | None]] = [(path.relative_to(root).as_posix(), path, path) for path in changed]
    if pr_body_file is not None:
        inputs.append(("PR body", Path(pr_body_file), None))
    for label, path, document_path in inputs:
        content, size = _read_bounded(path, _MAX_TOTAL_BYTES - consumed)
        consumed += size
        results.append(
            _verify_text(
                content,
                label=label,
                root=root,
                document_path=document_path,
                library_db=Path(library_db) if library_db is not None else None,
                knowledge_dir=Path(knowledge_dir) if knowledge_dir is not None else None,
                run_id=run_id,
            )
        )
    return {"verified": all(item["verified"] for item in results), "documents": results}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--base", required=True, help="Local ref to compare with the working tree")
    parser.add_argument("--pr-body-file", type=Path)
    parser.add_argument("--library-db", type=Path)
    parser.add_argument("--knowledge-dir", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    try:
        result = verify_inputs(
            args.repository_root,
            base=args.base,
            pr_body_file=args.pr_body_file,
            library_db=args.library_db,
            knowledge_dir=args.knowledge_dir,
            run_id=args.run_id,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"verification input error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
