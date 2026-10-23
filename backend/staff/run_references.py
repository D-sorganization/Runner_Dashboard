"""Typed, owner-authorized reads from run reference ledgers."""

from __future__ import annotations

from typing import Annotated, Literal

from library.codebase_retrieval_ledger import codebase_references_for_run
from library.db import init_library_db, library_db_path
from library.retrieval_ledger import authorize_run_owner, references_for_run
from pydantic import BaseModel, ConfigDict, Field
from staff.store import RunStore


class LibraryRunReference(BaseModel):
    kind: Literal["library"]
    node: str
    source_id: str
    work_id: int
    passage_id: int
    file_sha256: str
    page_start: int
    page_end: int
    citation: str
    model_config = ConfigDict(extra="forbid")


class CodebaseRunReference(BaseModel):
    kind: Literal["codebase"]
    node: str
    repo: str
    commit_sha: str
    relative_path: str
    line_start: int
    line_end: int
    content_hash: str
    citation: str
    model_config = ConfigDict(extra="forbid")


RunReference = Annotated[LibraryRunReference | CodebaseRunReference, Field(discriminator="kind")]


class StaffRunReferencesResponse(BaseModel):
    run_id: str
    references: list[RunReference]
    model_config = ConfigDict(extra="forbid")


def load_run_references(store: RunStore, *, run_id: str, principal_id: str) -> StaffRunReferencesResponse:
    """Authorize first, then return only stable identity fields from both ledgers."""
    authorize_run_owner(store, run_id=run_id, principal_id=principal_id)
    db_path = init_library_db(library_db_path())
    library_refs = references_for_run(db_path, run_id)
    codebase_refs = codebase_references_for_run(db_path, run_id)
    library_fields = (
        "node",
        "source_id",
        "work_id",
        "passage_id",
        "file_sha256",
        "page_start",
        "page_end",
        "citation",
    )
    codebase_fields = (
        "node",
        "repo",
        "commit_sha",
        "relative_path",
        "line_start",
        "line_end",
        "content_hash",
        "citation",
    )
    refs: list[RunReference] = [
        LibraryRunReference(kind="library", **{key: row[key] for key in library_fields}) for row in library_refs
    ]
    refs.extend(
        CodebaseRunReference(kind="codebase", **{key: row[key] for key in codebase_fields}) for row in codebase_refs
    )
    return StaffRunReferencesResponse(run_id=run_id, references=refs)
