# 0004. FastAPI as the backend framework (chosen over Flask)

## Status

Accepted — `backend/server.py` is a FastAPI application served by `uvicorn` on port 8321. Documented in `SPEC.md` §2.1.

## Context

The dashboard backend serves three classes of work simultaneously:

1. **GitHub REST proxy** — repeated, latency-sensitive calls to `api.github.com` for runners, workflow runs, repos, and queue state. Many `/api/*` endpoints fan out to several upstream calls per request.
2. **Local I/O and subprocess control** — reading systemd unit state, running `systemctl`, parsing report files, and watching local app health.
3. **Browser-facing JSON API + static SPA** — every dashboard tab issues `GET /api/*` and `POST /api/*` calls; the same process also serves the built Vite bundle from `dist/`.

The two practical contenders were Flask and FastAPI. The deciding criteria were:

- **Concurrency model.** The GitHub proxy benefits enormously from `async`/`await` and shared `httpx.AsyncClient` connection pools. Flask's default WSGI model is synchronous; getting comparable concurrency requires gevent / async patches that complicate deployment.
- **Type safety at boundaries.** The "Design by Contract" engineering principle requires every `POST` route to validate a typed payload. FastAPI's Pydantic-based dependency injection makes that the path of least resistance; Flask requires a separate validation library and hand-rolled error mapping.
- **OpenAPI / schema generation.** The dashboard publishes `GET /api/version` and a stable contract for cross-repo callers (Maxwell, Repository_Management). FastAPI emits OpenAPI for free; Flask needs an extension.
- **Background tasks and lifespan.** The autoscaler, queue cleanup, and metrics collection use FastAPI's `lifespan` context for startup/shutdown and `BackgroundTasks` for fire-and-forget work, both of which are first-class.
- **Familiarity and maintenance.** Both frameworks are well-known; FastAPI has more momentum in 2024-2026, more current docs for async patterns, and better typing support out of the box.

Flask still has real strengths — smaller surface area, simpler mental model, easier sync-only debugging — but every one of those is outweighed once async I/O is on the critical path, which it is here.

## Decision

Adopt **FastAPI** (with `uvicorn` as the ASGI server) as the single backend framework. Specifically:

- `backend/server.py` is the FastAPI app and registers all `/api/*` routes (with extracted routers under `backend/routers/*`).
- All I/O-bound route handlers are `async def` and use a shared `httpx.AsyncClient`.
- Request and response payloads are typed: dataclasses for the dispatch contract (see ADR 0002), Pydantic models for `POST` route bodies, plain `dict[str, Any]` only at GitHub-API boundaries that cannot be statically modelled.
- The same FastAPI process serves both `/api/*` JSON and the built frontend bundle from `dist/` — see ADR 0005.
- Tests live in `tests/api/` and use FastAPI's `TestClient` so route handlers are exercised in-process without booting `uvicorn`.

## Consequences

**Easier:**

- Async fan-out to the GitHub API is natural; one operator request can trigger many concurrent upstream calls without thread-pool gymnastics.
- Pydantic models give every `POST` route a typed pre-condition, satisfying the DbC engineering principle.
- OpenAPI schema is generated from the code, so the frontend type-generation pipeline (and external callers) consume a contract that cannot drift from the implementation.
- The lifespan hook gives a single, well-defined place to start/stop the autoscaler, queue cleanup tasks, and metrics collectors.
- Future async features (server-sent events, streaming responses, websockets for live runner state) are available without changing frameworks.

**Harder / cost:**

- The whole team must be comfortable with `async`/`await`. Mixing sync work into an async handler without `asyncio.to_thread(...)` blocks the event loop, which is a real foot-gun.
- ASGI-vs-WSGI matters at deploy time: the systemd unit (`deploy/runner-dashboard.service`) runs `uvicorn backend.server:app`, not `gunicorn`-style WSGI. Tooling that assumes WSGI (some profilers, some shared-hosting platforms) does not apply.
- FastAPI evolves faster than Flask and occasionally ships behaviour changes; the project pins versions in `requirements.txt` to insulate against this.
- Some library ecosystems (notably older synchronous DB drivers) need explicit `to_thread` wrapping to avoid blocking, which is friction the equivalent Flask code would not have.

If a future requirement demands a strictly synchronous worker (heavy CPU-bound report parsing, for example), the right answer is to push that work into a separate process or task queue rather than reverting the framework choice.
