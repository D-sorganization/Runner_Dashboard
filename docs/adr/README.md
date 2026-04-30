# Architecture Decision Records (ADRs)

This directory records significant architectural decisions made in the
`runner-dashboard` project. An **Architecture Decision Record** is a short,
dated document that captures one architecturally significant choice, the
context in which it was made, and the consequences (good and bad) of that
choice. ADRs let future contributors — human or AI — understand *why* the
system looks the way it does without having to reverse-engineer the answer
from the codebase.

We follow the lightweight Nygard / MADR template: each ADR has **Status**,
**Context**, **Decision**, and **Consequences** sections. Status values are
Proposed, Accepted, Deprecated, or Superseded by NNNN.

## Format

Each ADR is a Markdown file with the naming convention:

    NNNN-title-with-dashes.md

Numbers are zero-padded, monotonically increasing, and never reused — even
when an ADR is superseded, its file stays in place and the new ADR references
it.

## Index

1. [0001 — Migrate frontend to Vite + React + TypeScript](./0001-vite-react-typescript-frontend-migration.md) — replace the 18k-line `frontend/index.html` with a Vite-bundled, type-checked SPA.
2. [0002 — Dispatch envelope contract for hub-to-node commands](./0002-dispatch-envelope-contract.md) — the signed, allowlisted, audit-logged command envelope defined in `backend/dispatch_contract.py`.
3. [0003 — Plain-text lease comments for agent coordination](./0003-agent-coordination-lease-comment-format.md) — why agent coordination uses single-line `lease: <agent> expires <ts>` comments rather than YAML, JSON, or a side-channel database.
4. [0004 — FastAPI as the backend framework (chosen over Flask)](./0004-fastapi-over-flask.md) — async I/O, Pydantic-typed boundaries, and free OpenAPI motivated FastAPI over Flask for `backend/server.py`.
5. [0005 — Single-port architecture: FastAPI serves both API and frontend bundle](./0005-single-port-frontend-and-backend.md) — production runs on port 8321 only; FastAPI serves `/api/*` and the built Vite bundle from `dist/`.

## Template

```markdown
# NNNN. Title

## Status

- Proposed / Accepted / Deprecated / Superseded by [NNNN](./NNNN-title.md)

## Context

What is the issue that we're seeing that is motivating this decision or change?

## Decision

What is the change that we're proposing or have agreed to implement?

## Consequences

What becomes easier or more difficult to do because of this change?
```

## References

- Michael Nygard, ["Documenting Architecture Decisions"](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) (2011)
- [MADR — Markdown Architectural Decision Records](https://adr.github.io/madr/)
- [adr.github.io](https://adr.github.io/) — broader index of ADR practice
