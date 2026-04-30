# 0001. Migrate frontend to Vite + React + TypeScript

## Status

Accepted — first landed in commit `1655d07` ("fix: Phase 1 Vite + React TypeScript setup for issue #173", PR #267). Subsequent phases tracked under issue #173.

## Context

The original `runner-dashboard` frontend was a single, hand-written `frontend/index.html` file that grew to roughly 18,000 lines. Dependencies were loaded from CDN `<script>` tags, JSX was avoided in favour of `React.createElement` (`h(...)`) calls, and there was no build step. The earlier `CLAUDE.md` explicitly described this as "no build step", "no JSX", "no npm/node_modules", with state managed by React hooks and styling via inline styles.

That model worked while the dashboard was small. By early 2026 it had become the bottleneck for almost every frontend change:

- A single 18k-line file made code review, conflict resolution, and module ownership painful.
- Without TypeScript, large portions of the dispatch payload, machine registry, and remediation flows were duck-typed JavaScript with JSDoc, and regressions tended to surface only at runtime.
- New tabs (Mobile, Maxwell, Assessments, Queue Health) needed code splitting, lazy loading, and a PWA service worker — all awkward without a bundler.
- CDN `<script>` pinning created a soft supply-chain risk and made offline / installed-PWA usage unreliable.
- The frontend perf budget (`frontend/perf-budget.json`) and accessibility audits required tooling that assumed a real module graph.

Issue #173 proposed a phased migration to Vite + React + TypeScript so the frontend could be split into modules, type-checked, lazily bundled, and shipped as a real PWA without changing how the FastAPI backend serves it.

## Decision

Adopt **Vite** as the frontend build tool, **React** as the UI library, and **TypeScript** as the language for all new and migrated frontend code.

Concretely:

- Source lives under `frontend/` (`frontend/src/main.tsx` is the entry point, with `shell/`, `pages/`, `primitives/`, `hooks/`, `design/`, and a `legacy/` holding pen for un-migrated modules).
- `vite.config.ts` sets `root: 'frontend'` and `build.outDir: '../dist'`. The dev server runs on port `5173` and proxies `/api` to the backend; the production bundle is emitted to `dist/` and served by FastAPI from the same origin.
- `tsconfig.json` governs strict TypeScript across the frontend.
- Migration is phased (Phase 1 in PR #267): scaffolding lands first, then individual tabs are extracted from the legacy `index.html` into `pages/` modules over time. The legacy file remains importable via `frontend/src/legacy/` until each tab is fully migrated.
- The CDN-script / no-build conventions described in older `CLAUDE.md` revisions are superseded for any new code.

## Consequences

**Easier:**

- Per-tab code splitting, lazy loading, and tree-shaking are now first-class.
- TypeScript catches dispatch-envelope and `/api/*` payload mismatches at compile time; the same payload models can drive both backend Pydantic schemas and frontend types ("Reusable" engineering principle).
- PWA hardening (service worker, manifest, offline shell) is straightforward — see PR #268.
- Standard tooling (ESLint, Vitest, Playwright, Lighthouse CI) plugs in without bespoke wrappers.
- Frontend behaviour tests under `tests/frontend/` can import real modules instead of scraping a monolithic HTML file.

**Harder / cost:**

- A build step is now mandatory; `npm run build` must run before deploy and is part of the deployed-update flow.
- `node_modules` and a lockfile (`package-lock.json`) are now part of the repo's surface area, with the supply-chain risk that implies. Mitigated by pinning and pip-audit / npm-audit-style checks.
- Two parallel frontends (legacy `index.html` modules under `legacy/` and migrated `pages/`) coexist during the migration, increasing short-term complexity. Phased migration plan in `docs/vite-migration-plan.md` and issue #173 manage the transition.
- Contributors must learn Vite, TSX, and the project's design-token conventions; older "edit one file, refresh browser" workflow is gone for migrated tabs.

This ADR supersedes the "no build step / no JSX / no npm" guidance in earlier `CLAUDE.md` revisions for all frontend code other than what still lives under `frontend/src/legacy/`.
