"""Request handling for Alexandria citation and link verification."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from identity import Principal
from library.run_reference_recording import requested_run_id
from library.verify import verification_repo_root, verify_content


async def verify_library_request(request: Request, principal: Principal, db_path: Path) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="request body must be JSON") from exc
    if not isinstance(payload, dict) or set(payload) - {"content", "run_id", "local_only", "repo", "shared_only"}:
        raise HTTPException(status_code=422, detail="expected content and optional run_id/local_only/repo/shared_only")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be text")
    if payload.get("run_id") is not None and not isinstance(payload["run_id"], str):
        raise HTTPException(status_code=422, detail="run_id must be a string")
    if payload.get("local_only", False) is not True and payload.get("local_only", False) is not False:
        raise HTTPException(status_code=422, detail="local_only must be a boolean")
    if payload.get("shared_only", False) is not True and payload.get("shared_only", False) is not False:
        raise HTTPException(status_code=422, detail="shared_only must be a boolean")
    if payload.get("repo") is not None and not isinstance(payload["repo"], str):
        raise HTTPException(status_code=422, detail="repo must be a repository name")
    if payload.get("shared_only", False) and payload.get("repo") is not None:
        raise HTTPException(status_code=422, detail="repo verification is unavailable in shared_only mode")
    try:
        run_id = requested_run_id(request, principal, body_run_id=payload.get("run_id"))
        repo_root = verification_repo_root(payload.get("repo"))
        return await asyncio.to_thread(
            verify_content,
            db_path,
            content,
            run_id=run_id,
            repo_root=repo_root,
            local_only=payload.get("local_only", False),
            shared_only=payload.get("shared_only", False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
