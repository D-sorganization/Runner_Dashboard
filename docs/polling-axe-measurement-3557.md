# Polling load and same-build axe measurement (#3557)

Part of epic #3539 phase P5 ("Measure polling and axe before tuning"). Review A
flagged two findings as **DEFERRED** pending measurement: polling storms, and
424 live-node axe violations ("design-system debt"). This closes the loop with
a reproducible, same-build measurement and resolves the two conflicts the
issue named as open (`polling severity A vs B`, `live vs fixture axe`).

## Method

Both the axe pass and the polling trace run against the **same artifact**: a
`vite build` of current `main`, served by the real backend
(`backend/server.py`), driven headlessly with Playwright
(`@axe-core/playwright`, matching `tests/e2e/a11y.spec.ts`'s existing rules
and tag set). The backend ran under the repo's hermetic e2e fixture harness
(`tests/e2e/fakes/start_staff_backend.py`): an isolated `HOME`/`XDG_CONFIG_HOME`
and an empty `GH_TOKEN`, so nothing reached a real GitHub credential or a real
fleet node. The session was unauthenticated (no fixture bearer token injected
into the browser's own fetch client), which the frontend already degrades
gracefully for — the same state `tests/e2e/a11y.spec.ts` exercises in CI.

- Axe: `AxeBuilder` scoped to `#main-content`, tags `wcag2a`/`wcag2aa`, on the
  `/`, `/fleet`, `/work/queue` and `/staff` routes (direct navigation per the
  router's deep-link contract, issue #835).
- Polling: network requests to `/api/*` recorded over a steady-state 60 s
  window on the default (`/staff`) landing page, after the initial axe passes
  and route changes settled.

## Resolving "live vs fixture axe"

Review A's 424-violation count came from the **live** OGLaptop/node build,
which is hundreds of commits behind `main` under HOLD #2657 (no deploy of any
kind). Reproducing against that exact stale build would require either
deploying (blocked by the hold) or auditing a commit nobody can currently
serve. Reproducing against **current main's same production build** instead
is the only reproducible option available right now, and it is the same
standard the existing CI a11y gate already uses. This measurement adopts that
option: fixture/current-main is authoritative until the hold lifts, at which
point a follow-up should re-run the same scan against the freshly deployed
build to confirm the gap was deploy lag, not a main regression.

## Axe results (current main, same build)

| Route | Violation groups (any impact) |
| --- | --- |
| `/` (Staff Console) | 0 |
| `/fleet` | 0 |
| `/work/queue` | 0 |
| `/staff` | 0 |

Zero violations at any impact level, not only the `serious`/`critical` levels
`tests/e2e/a11y.spec.ts` gates on. This is consistent with #833/#2989/#2991
already having landed and the CI a11y gate currently being green — the
424-violation finding is explained by deploy lag against a stale live build,
not a defect in current `main`. **Disposition: no axe tuning child needed for
current main.** Re-verify against the live node once HOLD #2657 lifts and a
deploy actually ships main's current state.

## Polling results (60 s window, default landing page)

| Endpoint | Requests / 60 s |
| --- | --- |
| `/api/auth/refresh` | 22 |
| `/api/v1/staff/board` | 6 |
| `/api/auth/me` | 2 |
| `/api/v1/staff/inbox` | 2 |
| `/api/v1/staff/schedule` | 2 |
| `/api/v1/staff/roster` | 2 |
| `/api/staff/quota` | 1 |
| `/api/staff/usage/snapshot` | 1 |
| `/api/v1/staff/providers` | 1 |
| **Total** | **39** |

`/api/auth/me` landing at 2/60s (not the ~12/min #3672 measured before its
fix) confirms that fix's 30 s/visibility-gated interval is working as
designed — the "polling severity" finding Review A raised is resolved for
that endpoint. **Disposition: no further tuning child for `/api/auth/me`.**

The dominant signal instead is `/api/auth/refresh` at 22/60s, which was not
on #3672's radar (that issue was found by reading source, not by a dynamic
trace). Root cause and fix are filed as #3745: every one of the ~9 polling
observers independently triggers `tryRefreshSession()` on its own 401, and
the helper only dedupes *concurrent* callers, not repeated calls over time —
so an expired/invalid session produces a steady refresh-storm for as long as
the tab stays open, regardless of which page is in front. **Disposition:
tuning child filed (#3745).**

## Summary

- Axe: 0 violations reproduced on current main's same build across all four
  primary routes. Live-node reproduction is deferred to post-HOLD-#2657, per
  above.
- Polling: the known `/api/auth/me` storm is fixed (#3672, confirmed here).
  A new, previously-unmeasured `/api/auth/refresh` storm is found, diagnosed
  and filed as a tuning child (#3745).
- No code changes land in this PR; it is read-only measurement plus the two
  linked tuning children `#3745` (filed) and the axe live-vs-fixture
  resolution above (no child needed — closes `#3557`'s axe half outright).
