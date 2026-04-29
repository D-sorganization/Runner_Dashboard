# Mobile Native Shell Roadmap

## Decision

Ship the mobile operator console as a PWA first, then package the same web
build with Capacitor only after the mobile shell, offline cache, push
notifications, and authentication gates are stable in production.

React Native is not the preferred path for this epic. It would duplicate the
dashboard surface, split API contracts, and slow down remediation work that
depends on the existing browser UI. Tauri is also not the near-term mobile
path because this dashboard needs phone distribution and platform push support,
not a desktop wrapper.

## Rationale

The dashboard is currently a FastAPI-served single-page app with no frontend
build step. The Vite split tracked by issue #173 remains the precondition for
runtime TypeScript modules, per-tab lazy loading, and a clean Capacitor wrap.
Until that split lands, native-shell work should define contracts and avoid a
throwaway second frontend.

PWA-first keeps rollback simple: operators can refresh or revert the deployed
web app without app-store review. Capacitor remains the preferred packaging
candidate because it can wrap the same web build and add native push and
home-screen integration later without a UI rewrite.

## Packaging Plan

1. Finish the Vite migration so the dashboard has a normal web build artifact.
2. Keep mobile views behind web routes and shared API hooks; do not fork data
   contracts for mobile.
3. Add Playwright mobile viewport coverage for 375x812 and 412x915 before
   enabling any native wrapper.
4. Validate PWA install, offline snapshot, push notification, and auth gates
   in the web app.
5. Create a Capacitor proof of packaging that loads the production web build
   and calls only documented backend APIs.
6. Decide on store distribution after push reliability, device storage, and
   rollback procedures are verified on iOS and Android hardware.

## Go/No-Go Criteria

Go for Capacitor when:

- The PWA mobile shell passes Lighthouse PWA score 95 or higher.
- Fleet, Workflows, Remediation, Maxwell, and Reports are usable at 375x812
  and 412x915.
- Web Push and deep links work for completed agent dispatches.
- Offline fleet snapshots are visible and mutating actions surface pending or
  retry state.
- The same API contracts serve desktop, PWA, and packaged mobile clients.

No-go when:

- Mobile behavior requires a separate React Native screen or duplicated API.
- The Vite split is incomplete.
- Push or auth behavior requires privileged native code that cannot be audited
  and rolled back with the dashboard deployment.

## Follow-Up Issues

- M02: keep design tokens, type scale, motion, and breakpoints aligned between
  the current single-file frontend and future Vite modules.
- M03: introduce the runtime mobile shell after the Vite split creates
  importable shell components.
- M06: add push endpoints and subscription storage before native packaging.
- M16: add browser-based mobile viewport tests before any app-store build.
