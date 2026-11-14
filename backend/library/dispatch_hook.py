"""Staff dispatch hook for Alexandria library references (#2165 slice 6)."""

from __future__ import annotations

from staff.plan import RunRequest
from staff.roles import RoleSpec


def library_references_for_plan(role: RoleSpec, req: RunRequest) -> tuple[str, tuple[int, ...]]:
    if role.permissions.get("library_references") is not True or not req.repo:
        return "", ()
    from library.db import library_db_path
    from library.dispatch_refs import attach_library_references, reference_query_from_task

    db = library_db_path()
    if not db.is_file():
        return "", ()
    return attach_library_references(db, repo=req.repo, query=reference_query_from_task(req))
