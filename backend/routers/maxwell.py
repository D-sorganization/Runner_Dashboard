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
from dashboard_config import MAXWELL_API_TOKEN, MAXWELL_EXPLICITLY_CONFIGURED, MAXWELL_URL
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
    """Request body for POST /api/maxwell/chat.

    ``repo``/``repo_root`` (issue #838) scope a codebase Q&A session to a single
    repository. Both are optional so the existing fleet-status chat keeps working
    unchanged; when supplied they are forwarded to Maxwell-Daemon, which jails its
    agentic tools (read_file/grep_files/glob_files/run_bash) to that root. The
    daemon-side capability is tracked in Maxwell_Daemon#948; until it ships the
    daemon may answer 501 Not Implemented, which this proxy degrades gracefully.
    """

    message: str = Field(..., max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    # Friendly repo identifier (e.g. "Runner_Dashboard") shown in the picker.
    repo: str | None = Field(default=None, max_length=200)
    # Absolute filesystem root the daemon jails its codebase tools to.
    repo_root: str | None = Field(default=None, max_length=1000)


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


# Issue #963: start/stop drive the daemon via ``systemctl ... maxwell-daemon``,
# which matches Maxwell_Daemon's deploy/systemd/maxwell-daemon.service on Linux
# but is a silent no-op on Windows/WSL hosts where MD ships Launch-Maxwell.bat.
# The systemd unit name is part of the implicit RD↔MD contract; surface
# lifecycle availability explicitly instead of pretending the control worked.
MAXWELL_SYSTEMD_UNIT = "maxwell-daemon"


def _lifecycle_supported() -> bool:
    """Return True when systemd-based daemon lifecycle control is available.

    The Maxwell start/stop/restart controls shell out to ``systemctl``. On a host
    without systemd (Windows, bare WSL) those commands cannot manage the daemon,
    so the controls must report "unsupported on this platform" rather than
    silently failing. We treat the presence of a ``systemctl`` binary as the
    capability signal.
    """
    import shutil  # noqa: PLC0415

    return shutil.which("systemctl") is not None


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
    lifecycle_supported = _lifecycle_supported()
    if not lifecycle_supported:
        # No systemd → the start/stop controls cannot manage the daemon here.
        # Report this honestly instead of a misleading "probe error" (#963).
        service_detail = "systemd lifecycle control unavailable on this platform"
    else:
        try:
            # Note: using asyncio.to_thread to avoid blocking the event loop
            r = await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "is-active", MAXWELL_SYSTEMD_UNIT],
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

    # Contract negotiation (#956): when the daemon is reachable, surface its
    # advertised contract version and whether it is compatible with the version
    # this dashboard build targets. Best-effort — never fails the status probe.
    contract: dict[str, Any] = {
        "expected": _mc.EXPECTED_CONTRACT_VERSION,
        "daemon": None,
        "compatible": None,
    }
    if http_reachable:
        try:
            raw_version = await _mx_get("/api/version")
            ver = _mc.MaxwellVersionResponse.model_validate(_mc.strip_sensitive(raw_version))
            contract["daemon"] = ver.contract
            contract["compatible"] = ver.contract_compatible
        except Exception as e:  # noqa: BLE001 — negotiation is advisory here
            log.info("maxwell contract negotiation skipped: %s", str(e)[:120])

    return {
        "status": status,
        "binary_found": maxwell_binary is not None,
        "binary_path": maxwell_binary,
        "service_running": service_running,
        "service_detail": service_detail,
        "http_reachable": http_reachable,
        "http_detail": http_detail,
        "dashboard_url": base_url,
        # Issue #959: tell the UI whether the operator explicitly pointed RD at a
        # Maxwell endpoint. When False and the daemon is unreachable, the tab can
        # show "configuration needed" (set MAXWELL_URL/MAXWELL_PORT) instead of an
        # opaque connection error — the default localhost:8080 is only a guess.
        "configured": MAXWELL_EXPLICITLY_CONFIGURED,
        # Issue #963: whether start/stop/restart can actually manage the daemon
        # on this host. False on Windows/WSL without systemd.
        "lifecycle_supported": lifecycle_supported,
        "contract": contract,
        "deep_links": {
            "dashboard": base_url,
            "health": f"{base_url}/api/health",
            "logs": f"journalctl -u {MAXWELL_SYSTEMD_UNIT} -f",
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
    # Issue #963: refuse loudly on hosts without systemd instead of shelling out
    # to a systemctl that does not exist and reporting a generic 502. The daemon
    # lifecycle is only controllable where its systemd unit lives (Linux co-host).
    if not _lifecycle_supported():
        raise HTTPException(
            status_code=501,
            detail=(
                "Maxwell lifecycle control is unavailable on this platform "
                "(no systemd). Manage the daemon on its host directly "
                "(e.g. Launch-Maxwell.bat on Windows)."
            ),
        )

    code, out, stderr = await _run_cmd(["systemctl", action, MAXWELL_SYSTEMD_UNIT], timeout=15)
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


async def _validated_maxwell_status() -> _mc.MaxwellStatusResponse:
    """Fetch and validate MD ``/api/status``, merging ``/api/v2/status`` counts (#955).

    ``/api/status`` is the authoritative pipeline-state source (its discriminating
    ``pipeline_state`` field is required, so drift fails loudly as a 502). The
    richer ``/api/v2/status`` ``counts`` map refines the task tallies; it is
    best-effort — if it is unavailable or shape-shifted, the base status (with
    ``active_tasks`` derived from ``active_task_id``) is still returned.
    """
    raw = await _mx_get("/api/status")
    status = _mc.MaxwellStatusResponse.model_validate(_mc.strip_sensitive(raw))
    try:
        raw_v2 = await _mx_get("/api/v2/status")
        v2 = _mc.MaxwellStatusV2Response.model_validate(_mc.strip_sensitive(raw_v2))
        status.merge_v2_counts(v2)
    except (HTTPException, ValidationError) as exc:
        # v2 is an enrichment, not a hard dependency — log and keep base counts.
        log.info("maxwell v2 status enrichment unavailable: %s", str(exc)[:120])
    return status


@router.get("/version")
async def get_maxwell_version() -> dict:
    """Proxy GET /api/version from Maxwell-Daemon (contract-negotiated, #956).

    Surfaces the daemon's real version and its advertised contract version, plus a
    ``contract_compatible`` flag the Maxwell tab uses to show a degraded-mode
    banner on a major-version mismatch instead of rendering defaulted data.
    """
    raw = await _mx_get("/api/version")
    return _mc.MaxwellVersionResponse.model_validate(_mc.strip_sensitive(raw)).model_dump()


@router.get("/daemon-status")
async def get_maxwell_daemon_status_detail() -> dict:
    """Proxy GET /api/status from Maxwell-Daemon (pipeline state + counts, #955)."""
    return (await _validated_maxwell_status()).model_dump()


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
    """Proxy chat messages to Maxwell-Daemon while preserving streamed output.

    When ``repo``/``repo_root`` are supplied (issue #838) they are forwarded so the
    daemon scopes its agentic codebase tools to that repository. The companion
    daemon capability is tracked in Maxwell_Daemon#948; if the running daemon does
    not yet support codebase chat it answers ``501``, which we degrade into a clear,
    actionable message rather than a dead-end "HTTP 501".
    """
    path = "/api/chat"
    payload: dict[str, Any] = {
        "message": body.message,
        "history": body.history[-20:],
        "stream": True,
    }
    # Forward codebase-scoping fields only when present, so the existing
    # fleet-status chat payload is unchanged (additive, reversible — DbC).
    if body.repo:
        payload["repo"] = body.repo
    if body.repo_root:
        payload["repo_root"] = body.repo_root
    codebase_scoped = bool(body.repo or body.repo_root)

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
                    if resp.status_code == 501 and codebase_scoped:
                        # Daemon is reachable but the codebase Q&A capability
                        # (Maxwell_Daemon#948) is not deployed yet. Degrade
                        # gracefully instead of surfacing a raw 501.
                        yield (
                            "Codebase Q&A is not available on the connected Maxwell-Daemon yet. "
                            "Update the daemon to a build with codebase tools (Maxwell_Daemon#948), "
                            "or ask a fleet-status question instead."
                        )
                        return
                    if resp.status_code >= 400:
                        yield (
                            f"Maxwell-Daemon rejected the chat request (HTTP {resp.status_code}). "
                            "Check the daemon logs and that it is healthy, then retry."
                        )
                        return
                    async for chunk in resp.aiter_text():
                        if chunk:
                            yield chunk
        except httpx.TimeoutException:
            log.info("maxwell_proxy: path=%s status=%s", path, "timeout")
            yield "Maxwell-Daemon timed out while answering. It may be busy — retry in a moment."
        except httpx.ConnectError:
            log.info("maxwell_proxy: path=%s status=%s", path, "connect-error")
            yield (
                "Maxwell-Daemon is unreachable. Start it from Local Tools (or check its URL/token), "
                "then retry — your chat history is preserved."
            )
        except Exception as e:  # noqa: BLE001
            log.info("maxwell_proxy: path=%s status=%s", path, "error")
            yield f"Maxwell-Daemon could not be reached: {str(e)[:120]}"

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
    # Maxwell-Daemon exposes POST /api/control/{action} (not /api/v1/control);
    # see maxwell_daemon/api/routes/dispatch.py. Proxying to the v1 path 404'd
    # every pause/resume/abort (issue #952).
    path = f"/api/control/{action}"
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
    """Proxy GET /api/status (pipeline state + counts) from Maxwell-Daemon (#955)."""
    return (await _validated_maxwell_status()).model_dump()
