"""Proxy staff run details, events, and SSE streams across peer fleet nodes (SC-B7, Issue #1314).

Enables hub or peer nodes to transparently locate, inspect, cancel, and stream runs
that are executing on other machines across the fleet without returning 404.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from staff import fleet as staff_fleet

log = logging.getLogger("dashboard.staff.remote_runs")

# Positive cache entries expire quickly and are size-bounded so completed or
# moved runs cannot pin stale peer locations for the process lifetime.
_RUN_PEER_CACHE: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
_SESSION_PEER_CACHE: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
_RUN_PEER_CACHE_LIMIT = 512
_RUN_PEER_CACHE_TTL_SECONDS = 30.0
_LOOKUP_TIMEOUT_SECONDS = 2.5
_LOOKUP_CONCURRENCY = 8
_LOOKUP_SEMAPHORE = asyncio.Semaphore(_LOOKUP_CONCURRENCY)
_STREAM_ERROR_BODY_MAX_BYTES = 4096
_STREAM_ERROR_BODY_TIMEOUT_SECONDS = 2.5


class RemoteRunLookupUnavailable(Exception):
    """Peer lookup could not finish reliably within its bounded budget."""


class RemoteRunNotFound(Exception):
    """A cached remote owner no longer has the run."""


def _cached_peer(run_id: str) -> tuple[str, str] | None:
    entry = _RUN_PEER_CACHE.get(run_id)
    if entry is None:
        return None
    name, url, expires_at = entry
    if expires_at <= time.monotonic():
        _RUN_PEER_CACHE.pop(run_id, None)
        return None
    _RUN_PEER_CACHE.move_to_end(run_id)
    return name, url


def _remember_peer(run_id: str, peer_name: str, peer_url: str) -> None:
    _RUN_PEER_CACHE[run_id] = (peer_name, peer_url, time.monotonic() + _RUN_PEER_CACHE_TTL_SECONDS)
    _RUN_PEER_CACHE.move_to_end(run_id)
    while len(_RUN_PEER_CACHE) > _RUN_PEER_CACHE_LIMIT:
        _RUN_PEER_CACHE.popitem(last=False)


def _forget_peer(run_id: str) -> None:
    _RUN_PEER_CACHE.pop(run_id, None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _has_local_run_detail(data: Any, run_id: str) -> bool:
    if not isinstance(data, dict):
        return False
    run = data.get("run")
    return (
        isinstance(run, dict)
        and run.get("id") == run_id
        and isinstance(data.get("events"), list)
        and isinstance(data.get("attempts"), list)
    )


def _is_confirmed_local_miss(response: httpx.Response, run_id: str) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail == {"code": "local_run_not_found", "run_id": run_id}


async def _read_bounded_stream_error_body(response: httpx.Response) -> bytes | None:
    """Read a small error response under one deadline; never constrain live SSE."""
    deadline = asyncio.get_running_loop().time() + _STREAM_ERROR_BODY_TIMEOUT_SECONDS
    iterator = response.aiter_bytes(chunk_size=1024).__aiter__()
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            total += len(chunk)
            if total > _STREAM_ERROR_BODY_MAX_BYTES:
                return None
            chunks.append(chunk)
    except StopAsyncIteration:
        return b"".join(chunks)
    except TimeoutError:
        return None


async def find_remote_run(
    run_id: str,
    peers: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    """Locate which peer node owns the given run_id.

    Returns (peer_name, peer_url) or None if not found on any peer.
    """
    cached_peer = _cached_peer(run_id)
    if cached_peer is not None:
        return cached_peer

    nodes = peers if peers is not None else staff_fleet.peer_nodes()
    if not nodes:
        return None

    headers = staff_fleet.fleet_headers()

    async def _lookup(name: str, url: str) -> tuple[str, str] | None:
        acquired = False
        try:
            await asyncio.wait_for(_LOOKUP_SEMAPHORE.acquire(), timeout=_LOOKUP_TIMEOUT_SECONDS)
            acquired = True
            data = await asyncio.wait_for(
                staff_fleet.get_json(f"{url}/api/staff/local-runs/{run_id}?events=0", headers),
                timeout=_LOOKUP_TIMEOUT_SECONDS,
            )
            if _has_local_run_detail(data, run_id):
                return name, url
            raise RemoteRunLookupUnavailable(f"peer {name} returned an invalid local-run response")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                if _is_confirmed_local_miss(exc.response, run_id):
                    return None
                raise RemoteRunLookupUnavailable(
                    f"peer {name} does not support the safe local-run lookup contract; upgrade required"
                ) from exc
            raise RemoteRunLookupUnavailable(f"peer {name} returned HTTP {exc.response.status_code}") from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise RemoteRunLookupUnavailable(f"peer {name} lookup timed out") from exc
        except Exception as exc:  # noqa: BLE001
            raise RemoteRunLookupUnavailable(f"peer {name} lookup failed") from exc
        finally:
            if acquired:
                _LOOKUP_SEMAPHORE.release()

    tasks = [asyncio.create_task(_lookup(name, url)) for name, url in nodes.items()]
    if not tasks:
        return None
    try:
        done, pending = await asyncio.wait(tasks, timeout=_LOOKUP_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        failures = [task.exception() for task in done if not task.cancelled() and task.exception() is not None]
        matches = [task.result() for task in done if not task.cancelled() and task.exception() is None]
        for match in matches:
            if match is not None:
                _remember_peer(run_id, *match)
                return match
        if pending or failures:
            raise RemoteRunLookupUnavailable("peer run lookup did not complete within its bounded budget")
        return None
    finally:
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)


async def proxy_remote_run(
    run_id: str,
    events: int,
    peer_name: str,
    peer_url: str,
    caller_id: str = "",
) -> dict[str, Any]:
    """Fetch run details and event log from the peer node that owns it."""
    headers = staff_fleet.fleet_headers()
    if caller_id:
        headers["X-Staff-On-Behalf-Of"] = staff_fleet.sign_on_behalf_of(caller_id, surface="api")

    try:
        data = await staff_fleet.get_json(
            f"{peer_url}/api/staff/local-runs/{run_id}?events={events}",
            headers,
        )
        if not _has_local_run_detail(data, run_id):
            raise ValueError("peer returned an invalid local-run detail response")
        return data
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            if _is_confirmed_local_miss(exc.response, run_id):
                _forget_peer(run_id)
                raise RemoteRunNotFound(f"run {run_id} no longer exists on cached peer {peer_name}") from exc
            reason = "peer does not support the safe local-run endpoint; upgrade required"
        else:
            reason = f"peer returned HTTP {exc.response.status_code}"
        log.warning(
            "staff.remote_runs: peer %s unavailable for run %s: %s",
            peer_name,
            run_id,
            reason,
        )
        return {
            "run": {
                "id": run_id,
                "machine": peer_name,
                "status": "node_unreachable",
                "error": f"Node {peer_name}: {reason}",
                "failure_class": "node_offline",
            },
            "events": [],
            "attempts": [],
            "unreachable_since": _now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.remote_runs: peer %s unreachable for run %s: %s", peer_name, run_id, exc)
        return {
            "run": {
                "id": run_id,
                "machine": peer_name,
                "status": "node_unreachable",
                "error": f"Node {peer_name} unreachable since {_now_iso()}: {exc}",
                "failure_class": "node_offline",
            },
            "events": [],
            "attempts": [],
            "unreachable_since": _now_iso(),
        }


async def stream_remote_run(
    run_id: str,
    after: int,
    peer_name: str,
    peer_url: str,
    caller_id: str = "",
) -> AsyncIterator[str]:
    """Proxy an SSE event stream from a peer node to the caller."""
    headers = staff_fleet.fleet_headers()
    headers["Accept"] = "text/event-stream"
    if caller_id:
        headers["X-Staff-On-Behalf-Of"] = staff_fleet.sign_on_behalf_of(caller_id, surface="api")

    url = f"{peer_url}/api/staff/local-runs/{run_id}/stream?after={after}"
    timeout = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=None)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    confirmed_missing = False
                    if resp.status_code == 404:
                        error_body = await _read_bounded_stream_error_body(resp)
                        if error_body is not None:
                            error_response = httpx.Response(status_code=resp.status_code, content=error_body)
                            confirmed_missing = _is_confirmed_local_miss(error_response, run_id)
                    if confirmed_missing:
                        _forget_peer(run_id)
                    err_payload = json.dumps(
                        {
                            "run_id": run_id,
                            "machine": peer_name,
                            "status_code": resp.status_code,
                            "error": (
                                "run no longer exists on the peer"
                                if confirmed_missing
                                else "peer local-run endpoint unavailable; upgrade may be required"
                            ),
                        }
                    )
                    yield f"event: {'error' if confirmed_missing else 'node_unreachable'}\ndata: {err_payload}\n\n"
                    return
                async for line in resp.aiter_lines():
                    yield f"{line}\n"
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.remote_runs: remote SSE stream dropped for %s on %s: %s", run_id, peer_name, exc)
        unreach_payload = json.dumps(
            {
                "run_id": run_id,
                "machine": peer_name,
                "error": f"node {peer_name} unreachable since {_now_iso()}",
                "unreachable_since": _now_iso(),
            }
        )
        yield f"event: node_unreachable\ndata: {unreach_payload}\n\n"


async def proxy_remote_cancel(
    run_id: str,
    peer_name: str,
    peer_url: str,
    caller_id: str = "",
) -> dict[str, Any]:
    """Forward a cancel request for a remote run to the owning peer."""
    headers = staff_fleet.fleet_headers()
    if caller_id:
        headers["X-Staff-On-Behalf-Of"] = staff_fleet.sign_on_behalf_of(caller_id, surface="api")

    status, data = await staff_fleet.post_json(
        f"{peer_url}/api/staff/local-runs/{run_id}/cancel",
        {},
        headers,
    )
    if status == 404:
        if _is_confirmed_local_miss(httpx.Response(status_code=status, json=data), run_id):
            _forget_peer(run_id)
            raise RemoteRunNotFound(f"run {run_id} no longer exists on cached peer {peer_name}")
        raise RemoteRunLookupUnavailable(
            f"peer {peer_name} does not support the safe local-run endpoint; upgrade required"
        )
    if status >= 400:
        raise RemoteRunLookupUnavailable(f"peer {peer_name} returned HTTP {status} for local-run cancel")
    return data


def _has_local_session_chunks(data: Any, session_id: str) -> bool:
    return isinstance(data, dict) and data.get("session_id") == session_id and isinstance(data.get("items"), list)


def _is_confirmed_local_session_miss(response: httpx.Response, session_id: str) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    expected = {"code": "local_session_not_found", "session_id": session_id}
    return (body.get("detail") if isinstance(body, dict) else None) == expected


async def find_remote_session(session_id: str, peers: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Locate which peer node holds transcript chunks/metadata for ``session_id`` (#2867)."""
    if (entry := _SESSION_PEER_CACHE.get(session_id)) is not None:
        if entry[2] > time.monotonic():
            _SESSION_PEER_CACHE.move_to_end(session_id)
            return entry[0], entry[1]
        _SESSION_PEER_CACHE.pop(session_id, None)
    nodes = peers if peers is not None else staff_fleet.peer_nodes()
    if not nodes:
        return None
    headers = staff_fleet.fleet_headers()

    async def _lookup(name: str, url: str) -> tuple[str, str] | None:
        acquired = False
        try:
            await asyncio.wait_for(_LOOKUP_SEMAPHORE.acquire(), timeout=_LOOKUP_TIMEOUT_SECONDS)
            acquired = True
            path = f"{url}/api/v1/activity/local-sessions/{session_id}/chunks?after_seq=-1&limit=1"
            data = await asyncio.wait_for(staff_fleet.get_json(path, headers), timeout=_LOOKUP_TIMEOUT_SECONDS)
            if _has_local_session_chunks(data, session_id):
                return name, url
            raise RemoteRunLookupUnavailable(f"peer {name} returned an invalid local-session response")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and _is_confirmed_local_session_miss(exc.response, session_id):
                return None
            raise RemoteRunLookupUnavailable(f"peer {name} returned HTTP {exc.response.status_code}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RemoteRunLookupUnavailable(f"peer {name} lookup failed") from exc
        finally:
            if acquired:
                _LOOKUP_SEMAPHORE.release()

    tasks = [asyncio.create_task(_lookup(name, url)) for name, url in nodes.items()]
    try:
        done, pending = await asyncio.wait(tasks, timeout=_LOOKUP_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        failures = [t.exception() for t in done if not t.cancelled() and t.exception() is not None]
        for task in done:
            if not task.cancelled() and task.exception() is None and (match := task.result()) is not None:
                _SESSION_PEER_CACHE[session_id] = (*match, time.monotonic() + _RUN_PEER_CACHE_TTL_SECONDS)
                while len(_SESSION_PEER_CACHE) > _RUN_PEER_CACHE_LIMIT:
                    _SESSION_PEER_CACHE.popitem(last=False)
                return match
        if pending or failures:
            raise RemoteRunLookupUnavailable("peer session lookup did not complete within its bounded budget")
        return None
    finally:
        for task in (t for t in tasks if not t.done()):
            task.cancel()


async def proxy_remote_session_chunks(
    session_id: str, after_seq: int, limit: int, peer_name: str, peer_url: str
) -> dict[str, Any]:
    """Fetch transcript chunks and gaps from the peer node that owns ``session_id`` (#2867)."""
    try:
        path = f"{peer_url}/api/v1/activity/local-sessions/{session_id}/chunks?after_seq={after_seq}&limit={limit}"
        data = await staff_fleet.get_json(path, staff_fleet.fleet_headers())
        if not _has_local_session_chunks(data, session_id):
            raise RemoteRunLookupUnavailable(f"peer {peer_name} returned invalid session chunks")
        return data
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404 and _is_confirmed_local_session_miss(exc.response, session_id):
            _SESSION_PEER_CACHE.pop(session_id, None)
            raise RemoteRunNotFound(f"session {session_id} no longer exists on {peer_name}") from exc
        raise RemoteRunLookupUnavailable(f"peer {peer_name} returned HTTP {exc.response.status_code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RemoteRunLookupUnavailable(f"peer {peer_name} session chunks unavailable") from exc


async def stream_remote_session(
    session_id: str, after_seq: int, peer_name: str, peer_url: str, *, follow: bool = True
) -> AsyncIterator[str]:
    """Proxy a live or replay-only transcript SSE stream from a peer node (#2867)."""
    headers = {**staff_fleet.fleet_headers(), "Accept": "text/event-stream"}
    flw = str(follow).lower()
    url = f"{peer_url}/api/v1/activity/local-sessions/{session_id}/stream?after_seq={after_seq}&follow={flw}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=None)) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await _read_bounded_stream_error_body(resp) if resp.status_code == 404 else None
                    resp_obj = httpx.Response(status_code=404, content=body or b"")
                    missing = bool(body and _is_confirmed_local_session_miss(resp_obj, session_id))
                    if missing:
                        _SESSION_PEER_CACHE.pop(session_id, None)
                    err = json.dumps({"session_id": session_id, "machine": peer_name, "status_code": resp.status_code})
                    yield f"event: {'error' if missing else 'node_unreachable'}\ndata: {err}\n\n"
                    return
                async for line in resp.aiter_lines():
                    yield f"{line}\n"
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.remote_runs: remote session SSE dropped for %s on %s: %s", session_id, peer_name, exc)
        err = json.dumps({"session_id": session_id, "machine": peer_name, "unreachable_since": _now_iso()})
        yield f"event: node_unreachable\ndata: {err}\n\n"
