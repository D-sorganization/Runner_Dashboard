"""Attach Alexandria passages and preferential Google Drive folders to staff dispatch prompts (#2165, #2521)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from library.search import SearchOptions, search_passages
from library.sources import list_shared_drive_sources
from staff.plan import RunRequest

_DEFAULT_BUDGET = int(os.environ.get("LIBRARY_REFERENCES_BUDGET_TOKENS", "800"))


def reference_query_from_task(req: RunRequest) -> str:
    text = (req.prompt or "").strip()
    if text:
        return text[:800]
    if req.issue:
        return f"issue #{req.issue}"
    if req.pr:
        return f"pull request #{req.pr}"
    return (req.repo or "").strip()


def attach_library_references(
    db_path: Path,
    *,
    repo: str,
    query: str,
    budget_tokens: int = _DEFAULT_BUDGET,
) -> tuple[str, tuple[int, ...]]:
    if not query.strip() or not repo.strip():
        return "", ()
    drive_sources = list_shared_drive_sources(db_path, repo=repo)
    try:
        hits = search_passages(
            db_path,
            query,
            k=16,
            budget_tokens=budget_tokens,
            options=SearchOptions(repo=repo),
        )
    except ValueError:
        hits = []
    if not hits and not drive_sources:
        return "", ()
    lines = ["Library references (Alexandria):"]
    for src in drive_sources:
        fid = src["drive_folder_id"]
        tier = "preferential · priority 1" if src.get("preferential") else f"priority {src.get('priority', 1)}"
        lines.append(
            f"- [Google Drive ({tier})] {src['label']} (folder_id={fid}, "
            f"scope: parentId='{fid}' for user-Google-drive search_files/read_file_content)"
        )
    work_ids: list[int] = []
    for hit in hits:
        work_ids.append(hit.work_id)
        lines.append(f"- {hit.citation} {hit.cite_key} (work_id={hit.work_id})")
    q = quote(query[:200])
    lines.append(f"More: GET /api/v1/library/search?q={q}&repo={quote(repo)}&budget={budget_tokens}")
    return "\n".join(lines), tuple(dict.fromkeys(work_ids))
