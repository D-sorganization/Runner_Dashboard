from __future__ import annotations

import asyncio
import datetime as _dt_mod
import json as _json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import maxwell_contract as _mc
from dashboard_config import MAXWELL_API_TOKEN, MAXWELL_URL
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from identity import Principal, require_scope
from pydantic import BaseModel, Field, ValidationError
from security import safe_subprocess_env, sanitize_log_value

router = APIRouter(prefix="/api/maxwell", tags=["maxwell"])
log = logging.getLogger("dashboard")
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime


class MaxwellControlBody(BaseModel):
    action: str = Field(..., max_length=20)
    approved_by: str = Field(..., max_length=200)


class MaxwellDispatchBody(BaseModel):
    """Request body for POST /api/maxwell/dispatch (issue #349).

    Caller must supply ``confirmation_token``; proxy must not inject it.
    """

    confirmation_token: str = Field(..., min_length=1, max_length=512)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MaxwellPipelineControlBody(BaseModel):
    """Request body for POST /api/maxwell/pipeline-control/{action} (issue #349).

    Caller must supply ``confirmation_token``; proxy must not inject it.
    """

    confirmation_token: str = Field(..., min_length=1, max_length=512)


class MaxwellChatBody(BaseModel):
    message: str = Field(..., max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


def _maxwell_base_url() -> str:
    """Return the configured Maxwell-Daemon base URL."""
    return MAXWELL_URL


def _maxwell_api_token() -> str:
    """Return the configured Maxwell-Daemon API confirmation token."""
    return MAXWELL_API_TOKEN


def _maxwell_headers() -> dict:
    """Return auth headers for Maxwell-Daemon requests."""
    token = _maxwell_api_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def _mx_get(path: str, params: dict | None = None) -> dict:
    """GET helper for Maxwell proxy routes."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{_maxwell_base_url()}{path}",
                params=params,
                headers=_maxwell_headers(),
            )
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            from proxy_utils import _translate_upstream_response

            return _translate_upstream_response(resp, "maxwell")
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="maxwell timeout") from e
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail="maxwell connection error") from e
    except HTTPException:
        raise
    except Exception as e:
        log.info("maxwell_proxy: path=%s error=%s", path, str(e)[:80])
        raise HTTPException(status_code=502, detail="maxwell proxy error") from e


async def _run_cmd(cmd: list[str], timeout: int = 30, cwd: str | Path | None = None) -> tuple[int, str, str]:
    """Helper to run a shell command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=safe_subprocess_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode().strip(), stderr.decode().strip()
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return -1, "", "timeout"


@router.get("/status")
async def get_maxwell_status() -> dict:
    """Probe Maxwell-Daemon status and connectivity (Dashboard-facing)."""
    import shutil

    maxwell_binary = shutil.which("maxwell") or shutil.which("maxwell-daemon")

    # Check if maxwell service is running via systemd
    service_running = False
    service_detail = "unknown"
    try:
        # Note: using asyncio.to_thread to avoid blocking the event loop
        r = await asyncio.to_thread(
            subprocess.run,
            ["systemctl", "is-active", "maxwell-daemon"],
            capture_output=True,
            text=True,
            timeout=5,
            env=safe_subprocess_env(),
        )
        service_running = r.stdout.strip() == "active"
        service_detail = r.stdout.strip()
    except Exception as e:
        service_detail = f"probe error: {str(e)}"

    # Check HTTP reachability
    http_reachable = False
    http_detail = ""
    base_url = _maxwell_base_url()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/api/health", headers=_maxwell_headers())
            http_reachable = resp.status_code == 200
            http_detail = f"HTTP {resp.status_code}"
    except Exception as e:
        http_detail = str(e)

    status = "running" if (service_running or http_reachable) else "stopped"

    return {
        "status": status,
        "binary_found": maxwell_binary is not None,
        "binary_path": maxwell_binary,
        "service_running": service_running,
        "service_detail": service_detail,
        "http_reachable": http_reachable,
        "http_detail": http_detail,
        "dashboard_url": base_url,
        "deep_links": {
            "dashboard": base_url,
            "health": f"{base_url}/api/health",
            "logs": "journalctl -u maxwell-daemon -f",
        },
        "probed_at": datetime.now(UTC).isoformat(),
    }


@router.post("/control")
async def maxwell_control(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("maxwell.control")),  # noqa: B008,
) -> dict:
    """Start or stop Maxwell-Daemon service (confirmation required)."""
    body = await request.json()
    action = str(body.get("action", "")).strip()
    approved_by = str(body.get("approved_by", "")).strip()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=422, detail="action must be start, stop, or restart")
    if not approved_by:
        raise HTTPException(status_code=422, detail="approved_by required for privileged action")

    code, out, stderr = await _run_cmd(["systemctl", action, "maxwell-daemon"], timeout=15)
    log.info(
        "maxwell_control: action=%s approved_by=%s exit_code=%d",
        sanitize_log_value(action),
        sanitize_log_value(approved_by),
        code,
    )
    if code != 0:
        log.warning("maxwell %s failed: %s", action, stderr.strip()[:200])
        raise HTTPException(
            status_code=502,
            detail=f"maxwell {action} failed",
        )
    return {"status": action + "ed", "action": action, "approved_by": approved_by}


@router.get("/version")
async def get_maxwell_version() -> dict:
    """Proxy GET /api/version from Maxwell-Daemon (contract-filtered)."""
    raw = await _mx_get("/api/version")
    return _mc.MaxwellVersionResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/daemon-status")
async def get_maxwell_daemon_status_detail() -> dict:
    """Proxy GET /api/status from Maxwell-Daemon (pipeline state, contract-filtered)."""
    raw = await _mx_get("/api/status")
    return _mc.MaxwellStatusResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/tasks")
async def get_maxwell_tasks(limit: int = 20, cursor: str | None = None) -> dict:
    """Proxy GET /api/tasks from Maxwell-Daemon (contract-filtered)."""
    params: dict = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    raw = await _mx_get("/api/tasks", params=params)
    return _mc.MaxwellTaskListResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/tasks/{task_id}")
async def get_maxwell_task_detail(task_id: str) -> dict:
    """Proxy GET /api/tasks/{task_id} from Maxwell-Daemon (contract-filtered)."""
    raw = await _mx_get(f"/api/tasks/{task_id}")
    return _mc.MaxwellTaskDetailResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.post("/dispatch")
async def maxwell_dispatch_task(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("maxwell.control")),  # noqa: B008,
) -> dict:
    """Proxy POST /api/v1/tasks to Maxwell-Daemon (issue #349).

    Caller must supply ``confirmation_token``; server-side injection removed
    so the dashboard cannot silently bypass the daemon's confirmation gate.
    """
    import hashlib as _hashlib

    path = "/api/v1/tasks"
    raw_body = await request.json()

    # Validate caller-supplied confirmation_token (DbC, issue #349)
    try:
        validated_dispatch = MaxwellDispatchBody.model_validate(
            {
                "confirmation_token": raw_body.get("confirmation_token"),
                "idempotency_key": raw_body.get("idempotency_key"),
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="confirmation_token is required") from exc

    token_hash = _hashlib.sha256(validated_dispatch.confirmation_token.encode()).hexdigest()[:16]

    body = dict(raw_body)
    if not body.get("idempotency_key"):
        body["idempotency_key"] = validated_dispatch.idempotency_key or str(uuid.uuid4())
    # confirmation_token comes from the caller — do NOT overwrite with the API token

    hdrs = {"Content-Type": "application/json"}
    hdrs.update(_maxwell_headers())

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_maxwell_base_url()}{path}",
                content=_json.dumps(body),
                headers=hdrs,
            )
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            from proxy_utils import _translate_upstream_response

            raw = _translate_upstream_response(resp, "maxwell")
            result = _mc.MaxwellDispatchResponse.model_validate(_mc.strip_sensitive(raw)).model_dump(by_alias=False)
            log.info(
                "audit: maxwell_dispatch principal=%s task_id=%s confirmation_token_hash=%s",
                principal.id,
                result.get("task_id", "unknown"),
                token_hash,
            )
            return result
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="maxwell timeout") from e
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail="maxwell connection error") from e
    except HTTPException:
        raise
    except Exception as e:
        log.info("maxwell_proxy: path=%s error=%s", path, str(e)[:80])
        raise HTTPException(status_code=502, detail="maxwell proxy error") from e


@router.post("/chat", response_model=None)
async def maxwell_chat(
    body: MaxwellChatBody,
    *,
    principal: Principal = Depends(require_scope("operator")),  # noqa: B008
) -> StreamingResponse:
    """Proxy chat messages to Maxwell-Daemon while preserving streamed output."""
    path = "/api/chat"
    payload = {
        "message": body.message,
        "history": body.history[-20:],
        "stream": True,
    }

    async def stream_daemon_response() -> Any:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{_maxwell_base_url()}{path}",
                    json=payload,
                    headers=_maxwell_headers(),
                ) as resp:
                    log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
                    if resp.status_code >= 400:
                        yield f"Maxwell chat failed with HTTP {resp.status_code}."
                        return
                    async for chunk in resp.aiter_text():
                        if chunk:
                            yield chunk
        except Exception as e:  # noqa: BLE001
            log.info("maxwell_proxy: path=%s status=%s", path, "error")
            yield f"Maxwell-Daemon is unreachable: {str(e)[:120]}"

    return StreamingResponse(stream_daemon_response(), media_type="text/plain; charset=utf-8")


@router.post("/pipeline-control/{action}")
async def maxwell_pipeline_control(
    action: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("maxwell.control")),  # noqa: B008,
) -> dict:
    """Proxy POST /api/control/{action} to Maxwell-Daemon (issue #349).

    Caller must provide ``confirmation_token``; server-side injection removed.
    """
    if action not in ("pause", "resume", "abort"):
        raise HTTPException(status_code=422, detail="action must be pause, resume, or abort")
    path = f"/api/v1/control/{action}"
    raw_body = await request.json()

    # Validate caller-supplied confirmation_token (DbC, issue #349)
    try:
        MaxwellPipelineControlBody.model_validate({"confirmation_token": raw_body.get("confirmation_token")})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="confirmation_token is required") from exc

    body = dict(raw_body)
    # confirmation_token comes from the caller — do NOT overwrite with the API token

    hdrs = {"Content-Type": "application/json"}
    hdrs.update(_maxwell_headers())

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{_maxwell_base_url()}{path}",
                content=_json.dumps(body),
                headers=hdrs,
            )
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            from proxy_utils import _translate_upstream_response

            raw = _translate_upstream_response(resp, "maxwell")
            return _mc.MaxwellControlResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="maxwell timeout") from e
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail="maxwell connection error") from e
    except HTTPException:
        raise
    except Exception as e:
        log.info("maxwell_proxy: path=%s error=%s", path, str(e)[:80])
        raise HTTPException(status_code=502, detail="maxwell proxy error") from e


@router.get("/backends")
async def get_maxwell_backends() -> dict:
    """Proxy GET /api/v1/backends from Maxwell-Daemon (contract-filtered; secrets stripped)."""
    raw = await _mx_get("/api/v1/backends")
    return _mc.MaxwellBackendsResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/workers")
async def get_maxwell_workers() -> dict:
    """Proxy GET /api/v1/workers from Maxwell-Daemon (contract-filtered)."""
    raw = await _mx_get("/api/v1/workers")
    return _mc.MaxwellWorkersResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/cost")
async def get_maxwell_cost() -> dict:
    """Proxy GET /api/v1/cost from Maxwell-Daemon (contract-filtered)."""
    raw = await _mx_get("/api/v1/cost")
    return _mc.MaxwellCostResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/pipeline-state")
async def get_maxwell_pipeline_state() -> dict:
    """Proxy GET /api/status (pipeline state) from Maxwell-Daemon (contract-filtered)."""
    raw = await _mx_get("/api/status")
    return _mc.MaxwellStatusResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()
