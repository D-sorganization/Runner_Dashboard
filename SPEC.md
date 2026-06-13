# SPEC.md — D-sorganization Runner Dashboard

**Spec Version:** 2.5.107
**Application Version:** 4.8.0 (see `VERSION`)
**Last Updated:** 2026-06-12T00:00:00-07:00
**Status:** Active

- **2026-06-12 (2.5.107):** Hardened the fleet-node SSRF guard against
  DNS-suffix spoofing and rebinding (security, issue #931). `validate_fleet_node_url`
  in `backend/security.py` previously accepted any host ending in
  `.local`/`.internal`/`.ts.net` on suffix match alone, so a lookalike like
  `evil.example.ts.net` — or a `.internal` name resolving to a public or
  link-local IP — passed the guard. The suffix is now necessary but not
  sufficient: when a name resolves, every resolved IP must fall inside an allowed
  range (RFC 1918 private, loopback, or RFC 6598 CGNAT), with link-local
  (169.254.0.0/16, fe80::/10) and all public addresses explicitly rejected. Names
  that do not resolve are still accepted as config-time entries (a peer may be
  offline). A new `resolve_and_validate_fleet_host` helper returns the single
  validated IP an outbound caller should pin to, defeating DNS rebinding between
  the URL check and the request.
- **2026-06-12 (2.5.106):** Security/robustness hardening (issues #929, #930,
  #939). (#929) `safe_subprocess_env` (`backend/security.py`) stripped only a
  hand-maintained denylist, so any *new* `*_TOKEN`/`*_SECRET` env var leaked to
  every spawned subprocess by default; added a rot-proof suffix catch-all
  (`*_SECRET`/`*_TOKEN`/`*_KEY`/`*_PASSWORD`/`*_PASSWD`/`*_CREDENTIALS`) on top of
  the denylist (anchored at end-of-name, so `TOKEN_FILE_PATH` still passes
  through). (#930) The session cookie was unconditionally `https_only=True` and
  HSTS was always sent, but browsers drop Secure cookies on http:// origins — so
  session auth silently never worked on the documented plain-HTTP-over-tailnet
  deployment. Both are now gated on a new `DASHBOARD_TLS` flag
  (`dashboard_config.TLS_ENABLED`): default HTTP mode issues a usable cookie and
  sends no HSTS; TLS mode enforces Secure + HSTS. (#939) (a) `save_tokens`
  (`backend/identity.py`) is now an atomic tempfile + `os.replace` write like
  `save_principals`, so a crash mid-dump can't corrupt tokens.yml and lock
  everyone out; (c) the `POST /_drain` loopback guard (`backend/server.py`) is now
  an explicit `HTTPException(403)` instead of a bare `assert` (which `python -O`
  compiles out, letting any peer drain the server); (d) the subprocess timeout
  paths in `system_utils.run_cmd` and `queue_cleanup` now kill *then* `await
  wait()` and tolerate `ProcessLookupError`, preventing zombie/transport leaks.
  Tests in `tests/test_security.py`, `tests/test_middleware.py`,
  `tests/test_identity.py`, `tests/api/test_drain_mode.py`, and
  `tests/test_system_utils.py`.
- **2026-06-12 (2.5.104):** server.py god-module duplicate sweep (architecture,
  issue #941). Removed two body-identical twins from the ~2.3k-line wiring
  module: the `POST /api/launchers/generate` route handler (a shadowed dead copy
  of `routers/diagnostics.py`'s — FastAPI served the router's, never server.py's)
  and the unused `_normalize_repository_input` helper (a copy of the ones in
  `routers/assistant.py`/`routers/remediation.py` that server.py never called).
  Extended `tests/test_no_duplicate_top_level_functions.py` from legacy/App.tsx
  to all of `backend/**/*.py`: a new guard fails if server.py defines any function
  body-identical to another backend module, plus a backend-wide "no new
  body-identical duplicate" ratchet (pre-existing legitimate idioms allow-listed).
  Pruned the now-resolved launchers entry from the #941 route-uniqueness allowlist
  and corrected the CLAUDE.md architecture block (server.py size ~2.3k not ~6800;
  requirements.txt lives at the repo root, not `backend/`).
- **2026-06-12 (2.5.101):** Autoscaler correctness/robustness cluster (issues
  #932, #935, #936, #937). (#932) The autoscaler read leases from a repo-relative
  `config/leases.yml` that nothing ever wrote, so lease protection was a permanent
  no-op and actively-leased runners could be stopped; `_leased_runners`
  (`backend/autoscaler_sampling.py`) now resolves the path through
  `runner_lease._default_config_dir()` — the same `RUNNER_DASHBOARD_CONFIG_DIR`
  store the `LeaseManager` writer uses — so reader and writer can never drift.
  (#935) `_stop_unit`/`_start_unit` (`backend/autoscaler_systemd.py`) issued a
  blocking `systemctl stop`; a legitimate >=120s drain starved the systemd
  watchdog (WatchdogSec=120) and SIGABRTed the autoscaler mid-scale-down. Both now
  use `--no-block` plus an explicit client-call timeout and re-read unit state the
  next tick. (#936) `prune_expired` (`backend/runner_lease.py`) wrote a stale
  in-memory snapshot via an unlocked `save_leases` on every reaper tick, clobbering
  leases acquired by another process since the last load, and a crash mid-write
  reset the file to `[]`; pruning now goes through the locked
  `_atomic_read_modify_write` (re-read under exclusive lock) and all writes use
  temp-file + `os.replace` for crash-safety. (#937) Five autoscaler defects fixed:
  busy-query timeout no longer aborts the tick (fail-safe busy), exact lease-name
  match (not substring), one malformed lease record is skipped not fatal, the
  label-less default pool is exempt from start/stop label filters, and
  `ACTION_COOLDOWN_SECONDS` is enforced on the stop side. Tests across
  `tests/test_autoscaler_sampling.py`, `tests/test_runner_lease.py`,
  `tests/api/test_lease_pruning.py`, `tests/test_autoscaler_systemd.py`,
  `tests/test_autoscaler_busy.py`, and `tests/test_pool_autoscaler.py`.
- **2026-06-12 (2.5.99):** RD↔MD integration contract cluster (issues #959, #961,
  #963). (#959) `MAXWELL_PORT` defaulted to 8322 — a port that appears nowhere in
  Maxwell_Daemon (which serves on 8080) and that collided with the
  ControlTower-SSD pool's own `dashboard_url:8322` in `machine_registry.yml`, so a
  default deploy probed a second dashboard and misreported it as Maxwell. The
  default is now 8080 (the daemon's real port). A new `MAXWELL_EXPLICITLY_CONFIGURED`
  flag is surfaced on `GET /api/maxwell/status` as `configured` so the tab can show
  "configuration needed" instead of an opaque connection error when neither
  `MAXWELL_URL` nor `MAXWELL_PORT` is set. (#961) The task contract was mis-keyed
  and full of phantom fields: `MaxwellTaskListResponse` used `cursor` where MD emits
  `next_cursor`, and `MaxwellTaskItem`/`MaxwellTaskDetailResponse` modelled
  `updated_at`/`type`/`priority`/`tags`/`error`/`result_summary`, none of which MD's
  `TaskSummary`/`TaskDetail` produce. Models now mirror MD's real shapes
  (`{id, status, created_at}` + `transcript`/`artifacts` on detail), with `id`/`status`
  required so a defaulted task row is impossible. (#963) Maxwell lifecycle control
  (`POST /api/maxwell/control`) shelled out to `systemctl` unconditionally — a silent
  no-op on Windows/WSL hosts without systemd. It now returns HTTP 501 with an
  actionable message there, `GET /api/maxwell/status` reports `lifecycle_supported`,
  and the canonical `Maxwell_Daemon` repo slug replaces the broken `Maxwell-Daemon`
  links in `CLAUDE.md` and `docs/contracts/maxwell.md`. New tests in
  `tests/test_maxwell_contract.py` (incl. a #960 consumer-driven contract guard that
  fails loud on a simulated MD field rename) and `tests/test_maxwell_proxy.py`.
- **2026-06-12 (2.5.97):** Normalized the Maxwell backends contract for the
  daemon's current `/api/v1/backends` shape (integration, issue #954).
  Maxwell-Daemon returns a bare `list[str]` of provider names, while the
  dashboard proxy expected backend objects and turned the real response into a
  validation-backed HTTP 500. `MaxwellBackendsResponse` now accepts daemon string
  entries and converts them into the existing dashboard backend item shape
  (`name`, `type="unknown"`, `enabled=true`, optional `model`/`status`) before
  serialization. Contract docs and proxy/model regressions cover the real daemon
  list shape.
- **2026-06-12 (2.5.96):** Hardened three dispatch/credential security defects
  (security, issues #925, #926, #927). (1) `verify_approval_hmac`
  (`backend/dispatch/signing.py`) returned `True` for any confirmation lacking an
  `approval_hmac` ("backward compatibility"), so a forged approval with no HMAC
  verified and the envelope/action binding was effectively optional. It now fails
  closed: when a signing secret is configured an unsigned confirmation is
  rejected, with the legacy permissive behaviour available only behind the
  explicit, default-off `DISPATCH_ALLOW_UNSIGNED_APPROVAL` flag (which logs a
  deprecation warning). (2) `MAXWELL_API_TOKEN`
  (`backend/dashboard_config/__init__.py`) defaulted to the published string
  `"maxwell-local-secret"` — anyone reading the source could mint valid Maxwell
  bearer tokens. The default is now `""` (no token → no `Authorization` header,
  correct for a token-less daemon); the hardcoded string is gone. (3)
  `validate_local_path` (`backend/security.py`) resolved user input (following
  symlinks) but compared against an UNresolved `allowed_root`, so a symlinked root
  rejected legitimate paths and could let crafted symlinks pass containment. It
  now resolves both sides (resolved-vs-resolved). Each fix ships fail-loud tests.
- **2026-06-12 (2.5.95):** Fixed the autoscaler singleton lock falling through to
  an alternate path when the primary lock was HELD (correctness, issue #933).
  `_acquire_lock` (`backend/runner_autoscaler.py`) caught every `OSError` from the
  candidate-path loop and continued to the next path — but `BlockingIOError`
  ("lock held" from `flock(LOCK_NB)`) is an `OSError` subclass, so a second
  autoscaler instance failed the primary lock and silently acquired a DIFFERENT
  lock file and ran anyway, allowing two concurrent autoscalers to
  double-stop/double-start runners. The open/makedirs step (path unusable →
  fall through) is now separated from the flock step: a held lock raises
  `BlockingIOError` and the process exits `75` (EX_TEMPFAIL) immediately rather
  than trying an alternate path; only genuine path-unusable errors
  (`PermissionError`/`ENOENT`) fall through. Linux-only fail-loud tests added in
  `tests/test_autoscaler_singleton_lock.py`.
- **2026-06-12 (2.5.94):** Fixed branch-substring stale-run classification that
  auto-cancelled legitimate human CI (correctness, issue #934).
  `classify_stale_run` (`backend/queue_cleanup.py`) flagged any branch CONTAINING
  `agent`/`worktree`/`wt-`/`patch-`/`run-` as an abandoned-agent run with
  `safe_to_cancel=True`, so ordinary branches like `fix/rerun-tests`,
  `feat/dispatch-fix`, and `feature/user-agent-header` were reaped by the
  scheduled stale-job purge precisely during backlogs. Classification is now
  anchored to path-segment / prefix boundaries (first segment is an agent
  namespace `agent`/`codex`/`jules`/`worktree`, or starts with `wt-`/`patch-`/
  `run-`) AND requires a corroborating bot actor when the triggering actor is
  known — a human pushing to an agent-named branch is no longer auto-cancellable.
  The scan caller now passes `triggering_actor`/`actor` login into the
  classifier. Fail-loud tests added in `tests/test_queue_cleanup.py`.
- **2026-06-12 (2.5.92):** Made the Maxwell read-path contract real — the
  `/api/version`, `/api/status`, and `/api/v1/workers` consumer models in
  `backend/maxwell_contract.py` previously modelled an imaginary shape with zero
  overlapping keys against the daemon, so every field silently defaulted and the
  Maxwell tab perpetually showed "unknown / 0 tasks" and an empty worker list
  (integration, issues #955, #956, #958). The models now validate the daemon's
  REAL shapes: `/api/version` returns `{daemon, contract}` (both required;
  `daemon` mirrors to `version`, `contract_compatible` computed against
  `EXPECTED_CONTRACT_VERSION="2.0.0"`); `/api/status` returns the daemon's
  `pipeline_state` / `active_task_id` / `gate` / `sandbox` (`pipeline_state`
  required, mapped to `state`, `paused` derived), with task counts merged from
  `/api/v2/status` `counts`; `/api/v1/workers` returns
  `{worker_count, queue_depth}` (`worker_count` required, mirrored to `total`).
  The discriminating field of each is required so contract
  drift fails loudly (`ValidationError` surfaced as `502`) instead of defaulting.
  `GET /api/maxwell/status` now surfaces a `contract` negotiation block
  `{expected, daemon, compatible}` so the tab can show an incompatibility banner
  (#956). `docs/contracts/maxwell.md` bumped from "v1" to `2.0.0` with the real
  shapes. Existing contract/proxy tests updated off the imaginary shapes and new
  fail-loud / mapping tests added in `tests/test_maxwell_contract.py`.
- **2026-06-12 (2.5.91):** Added a structural authentication perimeter so every
  `/api/*` route is authenticated by default rather than opt-in per route
  (security, issues #924 and #928). A fail-closed middleware
  (`middleware.auth_perimeter_check`, registered before `SessionMiddleware` so the
  session is resolved) rejects any non-exempt `/api/*` request that does not
  resolve to a principal (service token, session, or gated loopback admin) with
  `401` before the handler runs; it defers to the route dependency when a test
  installs an `app.dependency_overrides` for the auth dependency, so production is
  always live while tests keep injecting identities. The exempt set
  (`_AUTH_EXEMPT_PATHS`) is an explicit, reviewed allowlist (health, auth
  handshake, logout, signed Linear webhook + its health probe); alternate-auth
  surfaces that enforce their own equally-strong check are listed in
  `_ALT_AUTH_EXEMPT_PREFIXES` (`/api/fleet/dispatch/*` HMAC envelopes,
  `/api/orchestrator/*` Conductor admission gate, `/api/credentials/*`
  loopback-only writes). Routes that previously shipped unauthenticated now carry
  an explicit dependency: `POST /api/metrics/web-vitals` (#928),
  `POST /api/runner-routing-audit/refresh` (both registrations, #928),
  `POST /api/runners/{id}/diagnostics` (#928),
  `POST /api/autoscaler/pools/{pool}/config` (#924), and
  `POST /api/linear/sync/poll` (#924). A new `tests/api/test_structural_auth_perimeter.py`
  walks `app.routes` and fails the build if any mutating `/api/*` route is neither
  auth-protected nor on a documented exempt list, so the perimeter cannot silently
  regress to opt-in.
- **2026-06-12 (2.5.90):** Fixed the dead Maxwell pipeline-control proxy path
  (integration, issue #952). `POST /api/maxwell/pipeline-control/{action}`
  proxied to `/api/v1/control/{action}`, which Maxwell-Daemon does not expose —
  every pause/resume/abort from the dashboard 404'd. The proxy now targets MD's
  real route `POST /api/control/{action}`, and `MaxwellControlResponse` carries
  MD's `{action, applied_at, previous_state}` shape (with `action` required so a
  mismatched payload fails loudly; legacy `status`/`message` kept optional for
  backward compatibility). `docs/contracts/maxwell.md` updated to match. Part of
  the RD↔MD contract epic (#964); the MD side already serves port 8080 /
  contract 2.0.0.
- **2026-06-12 (2.5.89):** Enforced intra-fleet authentication on the hub
  (security, issue #922). Added the `require_fleet_peer` dependency
  (`backend/identity.py`): a hub-reachable fleet route is accepted only when the
  caller presents a valid principal OR `Authorization: Bearer <HUB_FLEET_TOKEN>`
  matching the hub's configured token via `hmac.compare_digest` (constant-time).
  Applied to `GET /api/fleet/status`. Policy: fleet reads are token-gated when
  `HUB_FLEET_TOKEN` is set (unauthenticated callers get `401`) and tailnet-public
  when it is unset (backward-compatible no-op for single-node deployments).
  `docs/runbooks/hub-credentials.md` now describes the enforced behavior instead
  of claiming validation that did not exist. The structural "all `/api/*` routes
  authenticated" follow-up is tracked by #924.
- **2026-06-12 (2.5.88):** Collapsed the two divergent `proxy_to_hub`
  implementations into one (security, issue #923, re-opens #347). `backend/server.py`
  previously defined its own `proxy_to_hub` that forwarded ALL caller headers to
  the hub except `host`/`content-length` — laundering operator session cookies,
  `Authorization`, `X-API-Key`, and `X-CSRF-Token` upstream — and that copy was the
  one dependency-injected into the deployment / orchestration / orchestration-node
  routers. The server-side body is deleted; `server.proxy_to_hub` and
  `server._should_proxy_fleet_to_hub` are now thin re-exports of the single
  header-stripping implementation in `proxy_utils`, which strips the sensitive
  headers and injects the intra-fleet `HUB_FLEET_TOKEN`. A regression test asserts
  that for every hub-proxying route no operator credential reaches the hub.
- **2026-06-12 (2.5.87):** Removed the duplicate `GET /api/system` and
  `GET /api/fleet/status` route registrations in `backend/metrics.py`
  (architecture/correctness, issue #940). Because the metrics router is included
  before `routers.system` and `routers.fleet`, FastAPI's first-match-wins routing
  served the metrics copies and shadowed the maintained implementations — silently
  killing the `FleetEventPoller` wiring in `routers/fleet.py` that feeds
  `/api/events` (issue #863), and re-registering `/api/system` over
  `routers/system.py`. The canonical handlers now own those paths and
  `metrics.py` no longer lazy-imports from `backend.server` (circular import
  removed). The unique `GET /api/disk/pool-pressure` route is unchanged. A new
  route-uniqueness invariant (`tests/api/test_route_uniqueness.py`) fails on any
  duplicate `(method, path)` pair except the explicitly-tracked god-module twins
  pending issue #941.
- **2026-06-12 (2.5.86):** Closed unauthenticated remote admin via the dev-login
  endpoint (security, issue #921). `GET /api/auth/dev-login` — which mints an
  admin session for the first human principal — is now gated on BOTH a loopback
  transport peer AND an explicit `DASHBOARD_DEV_LOGIN=1` opt-in; any request
  failing either gate gets a `404` (indistinguishable from "not present" to a
  remote scanner). The `__main__` uvicorn bind now uses `dashboard_config.HOST`
  (honoring `DASHBOARD_HOST`) instead of a hardcoded `0.0.0.0`; the default
  still resolves to `0.0.0.0` so existing deployments are unchanged.
- **2026-06-12 (2.5.85):** Closed the dispatch HMAC signature-gate bypass
  (security, issue #919). `CommandEnvelope.from_dict` no longer runs the
  auto-signing path used for locally minted envelopes: an inbound wire body is
  reconstructed verbatim and a new `signature_authentic` flag records whether a
  non-empty signature was actually present. `validate_envelope_crypto` now
  rejects any envelope whose signature was absent on the wire
  (`"envelope signature missing"`), so `POST /api/fleet/dispatch/submit` returns
  400 for signature-less or empty-signature envelopes instead of the server
  minting a valid signature on the caller's behalf. Locally minted,
  server-originated envelopes still auto-sign and round-trip unchanged.
- **2026-06-12 (2.5.84):** Authenticated the agent-launcher control surface
  (security, issue #920). Every `/api/agent-launcher/*` route now requires an
  authenticated principal; the mutating routes (`PUT /config`, `POST /start`,
  `POST /stop`, `POST /run-once`) additionally require the privileged
  `system.control` scope. Previously these routes — which spawn code-executing
  agent processes and write attacker-controllable JSON into the operator's home
  directory — were completely unauthenticated, so any LAN/Tailscale peer
  reaching the dashboard port could launch or reconfigure agents (RCE-adjacent).
  Unauthenticated requests now return 401 and under-scoped principals get 403.
- **2026-06-10 (2.5.83):** Corrected queue-diagnosis target classification for
  fleet jobs whose GitHub job metadata contains only custom `d-sorg-fleet*`
  labels. These jobs are now counted as self-hosted fleet work instead of
  GitHub-hosted waits, so the diagnostic bottleneck reflects local runner
  capacity when custom fleet labels route the job.
- **2026-06-10 (2.5.82):** Replaced the placeholder queue-diagnosis response
  with live queue and runner analysis. `GET /api/queue/diagnose` now samples
  queued workflow jobs through the shared authenticated GitHub API path,
  summarizes online/busy/idle/offline runner capacity, distinguishes
  GitHub-hosted, generic self-hosted, and `d-sorg-fleet` waits, reports
  unroutable label sets, preserves bounded partial-failure errors, and caches
  the diagnosis briefly so operators can explain queue stalls without adding
  avoidable GitHub API pressure.
- **2026-06-10 (2.5.81):** Fixed runner inventory visibility for fleets larger
  than GitHub's default first page. Dashboard runner, health, stats, quick
  dispatch, fleet control, diagnostics, group, and orchestration capacity paths
  now share a paginated GitHub runner inventory helper, so `/api/runners` and
  `/api/health` report all registered runners instead of stopping at 30 when
  the organization has additional DeskComputer/OGLaptop/MATLAB capacity. CI
  routing now keeps Python dependency-heavy jobs on `d-sorg-fleet-fast-io` and
  Docker image scans on runners with both `d-sorg-fleet-docker` and
  `d-sorg-fleet-fast-io`, preventing broad-fleet dispatch onto light laptop
  runners that lack healthy Docker or cache state.
- **2026-06-10 (2.5.80):** Routed queue-read GitHub calls through the shared
  GitHub App API client instead of shelling out to `gh api`. The dashboard queue
  view now uses the same authenticated client path as health checks, preserves
  per-job queued depth, and keeps stale auth or transient repo failures from
  silently flattening the queue to zero. The OGLaptop registry entry now uses
  its Tailscale MagicDNS dashboard URL so Tailscale Serve routes fleet health
  calls to the running backend instead of returning the IP-host 404 path.
- **2026-06-10 (2.5.79):** Restored post-merge `main` CI compatibility for the
  local-runner fleet. The 500-line source cap now keeps the existing legacy
  frontend over-cap baseline explicit so `push` validation does not fail on
  unrelated historical debt, while pull requests still check changed source
  files. `local-only-runner-guard.yml` now runs on `d-sorg-fleet` itself, so
  workflow-routing validation no longer depends on hosted GitHub runners.
- **2026-06-10 (2.5.78):** Hardened fleet dashboard routing for the
  GitHub-primary/Forgejo-ready fleet posture. `backend/machine_registry.yml`
  now routes ControlTower pool dashboards through Tailscale MagicDNS
  (`controltower.tail2bbcc7.ts.net`) instead of stale local/offline addresses,
  marks the live ControlTower host as preferred, and keeps the legacy monitoring
  node non-preferred. Node-system proxy calls now have a 30 s budget so loaded
  DeskComputer and ControlTower runner hosts can finish process enumeration
  without false 504s. `deploy/wsl-mirrored-port-helper.sh` now detects and
  clears Tailscale Serve HTTP/HTTPS/TCP bindings by protocol before restoring
  the WSL dashboard HTTP binding. The `security-scan` CI job now runs
  `pip-audit` from an isolated no-cache audit venv against `requirements.txt`,
  avoiding runner-side project-venv cache corruption while preserving the
  dependency audit gate.
- **2026-06-10 (2.5.77):** Added a non-matrix `tests` aggregate CI context
  after the Python matrix job so branch protection consumes the same green test
  result as `tests (3.11)` without synthetic statuses.
- **2026-06-03 (2.5.76):** Migrated the modern `DesktopShell` chrome onto the
  #834 scoped-class path. Shell layout, topbar, action cluster, main scrolling
  region, and active action state now render through `desktop-shell__*` and
  `shell-action--active` classes instead of inline JSX styles.
- **2026-06-03 (2.5.75):** Migrated the Help/About dialog shell onto the #834
  scoped-class path. The trigger, dialog tabs, version metadata, quick-link
  chips, checklist, and keyboard shortcut rows now render through
  `help-about__*` classes instead of inline JSX styles.
- **2026-06-03 (2.5.74):** Migrated the Analysis Reports panel onto the #834
  scoped-class path. The report sidebar title, empty states, reader header,
  selected-date badge, chart frame, and loading placeholder now use
  `reports-*` and `report-*` classes instead of inline layout styles.
- **2026-06-03 (2.5.73):** Migrated the `EventLog` primitive onto the #834
  scoped-class path. Toolbar, filter, row, chip, detail, empty-state, and
  accessibility presentation now render through `event-log__*` and
  `visually-hidden` classes instead of JSX style helpers, while virtualization
  dimensions remain isolated behind refs.
- **2026-06-03 (2.5.72):** Migrated the Events tab wrapper onto the #834
  scoped-class path. The dedicated Events tab and compact Overview event
  section now own layout, title, and degraded-state presentation through
  `events-tab__*` and `overview-event-section` classes instead of inline styles.
- **2026-06-03 (2.5.71):** Migrated the assistant Markdown renderer onto the
  #834 scoped-class path. Code blocks, lists, headings, paragraphs, inline code,
  and links now render through `assistant-markdown__*` classes instead of inline
  style objects, with renderer tests and static guards covering the contract.
- **2026-06-03 (2.5.70):** Migrated the shared `Stat` metric card value
  coloring onto the #834 token-class path. Known accent token values now map to
  scoped `stat-value--accent-*` classes, removing the remaining inline value
  color style from `components/Stat.tsx`.
- **2026-06-03 (2.5.69):** Migrated AlarmPanel onto the #834 primitive/token
  path. Fleet alarm severity chips now render through `Badge`, status/row/dot
  severity styling is owned by scoped `alarm-panel__*` classes, and the
  component no longer carries inline `CSSProperties` style helpers.
- **2026-06-03 (2.5.68):** Migrated Theme Settings onto the #834
  primitive/token path. The default accent action now uses `TouchButton`, accent
  swatches render through scoped `theme-settings__*` classes backed by theme
  tokens, and concrete accent preset values live in `design/accentPresets.ts`
  instead of inline TSX style/color literals.
- **2026-06-03 (2.5.67):** Migrated the desktop shell Density Toggle onto the
  #834 primitive/token path. The compact/comfortable control now renders
  through `TouchButton`, preserves `aria-pressed` via the primitive `pressed`
  prop, and uses scoped `density-toggle__*` classes instead of inline spacing
  and label styles.
- **2026-06-03 (2.5.66):** Migrated the header Quick Dispatch popover onto
  the #834 primitive/token path. The trigger and action controls now render
  through `TouchButton`, the primitive accepts button refs for popover
  outside-click handling, and scoped `quick-dispatch__*` classes own popover,
  field, and status presentation instead of inline style objects.
- **2026-06-03 (2.5.65):** Migrated the Push Notifications settings surface
  onto the #834 primitive/token path. Unsupported/config-missing and load-failure
  states now render through `EmptyState`; subscribe/unsubscribe actions use
  `TouchButton`; and scoped `push-settings__*` classes own page and topic
  presentation instead of inline styles or hand-written touch button classes.
- **2026-06-03 (2.5.64):** Migrated the Linear Integration Setup surface onto
  the #834 primitive/token path. Workspace auth status now renders through
  `Badge`; load failures and empty workspace states use `EmptyState`; webhook
  copy uses `TouchButton`; and scoped `linear-setup__*` classes own panel,
  workspace, field, loading, and instruction presentation.
- **2026-06-03 (2.5.63):** Migrated the Agent Dispatch mobile remediation
  flow onto the #834 primitive/token path. Failed-run empty state and load
  failures now render through `EmptyState`; dispatch/retry actions use
  `TouchButton` without inline width styles; and scoped `agent-dispatch__*`
  classes own loading, error, and full-width action presentation.
- **2026-06-03 (2.5.62):** Migrated the Conductor admission-gate surface onto
  the #834 primitive/token path. Queue mode and provider mix now render through
  `Badge`; disabled/error states use `EmptyState`; pause/resume/drain actions
  use `TouchButton`; and scoped `conductor__*` classes own stat, budget, section,
  and control presentation instead of page-level inline styles.
- **2026-06-03 (2.5.61):** Migrated the Tests tab onto the #834 primitive/token
  path. CI status, heavy-test dispatch state, and recent-run conclusions now
  render through `Badge`; CI loading uses `EmptyState`; rerun and heavy dispatch
  actions use `TouchButton`; and scoped `tests-tab__*` classes own table wraps,
  links, headings, and status presentation instead of page-level inline styles.
- **2026-06-03 (2.5.60):** Migrated the Runner Schedule capacity editor onto
  the #834 primitive/token path. The page now uses `Badge` for scheduler and
  saving state, `TouchButton` for refresh/save/apply actions, `EmptyState` for
  missing schedule/error surfaces, and scoped `runner-schedule__*` classes for
  table/input/footer presentation instead of page-level inline styles.
- **2026-06-02 (2.5.59):** Completed the PyJWT 2.13.0 security remediation by
  bumping the pin in `requirements.txt` and `requirements.lock.txt`, which the
  2.5.58 change left at 2.12.0 alongside the `pyproject.toml`/`uv.lock` updates.
  `security-scan` (pip-audit on `requirements.txt`) and `quality-gate` now read
  2.13.0 from every dependency manifest, fully clearing the PYSEC-2026 PyJWT
  advisories across the resolved environment.

- **2026-06-02 (2.5.58):** Security audit findings for PyJWT are remediated by
  upgrading `PyJWT[crypto]` from 2.12.0 to 2.13.0 in both `pyproject.toml` and
  `uv.lock`, clearing the PYSEC-2026 advisories reported by `pip-audit` in
  `quality-gate` and `security-scan`. The mobile queue Vitest coverage now
  asserts the current `Queue health summary` accessibility label, matching the
  dashboard integrity contract restored in 2.5.57.

- **2026-06-02 (2.5.57):** The legacy dashboard API migration now preserves the
  shared frontend integrity contracts after the `legacyFetch` wrapper adoption.
  `tests/test_frontend_integrity.py` accepts both legacy object-literal and TSX
  prop source shapes for sortable headers and ARIA labels while still enforcing
  the exact labels and response-check ordering. `frontend/src/pages/Queue/Mobile.tsx`
  restores the "Queue health summary" accessible section label,
  `frontend/src/main.tsx` restores the root Suspense "Loading dashboard..."
  status text, and `frontend/src/legacy/App.tsx` again renders the
  `fleet-hero__alerts` surface from the extracted fleet-alert rollup so the
  overview hero keeps user-visible alert detail.

- **2026-06-02 (2.5.56):** Restored the Docker runtime image to the pinned
  Python 3.12 base after a dependabot bump to `python:3.14-slim` re-broke the
  `docker-build-scan` job and the `test_dockerfile_pins_base_image_to_digest`
  deploy-hardening check. Python 3.14 has no prebuilt wheels yet for
  `pydantic-core`, `jiter`, `uvloop`, `watchfiles`, and `cffi`, so the image
  build fell back to source compilation and failed; `pyproject.toml`
  (`>=3.11,<3.14`) and `uv.lock` already constrain the runtime to 3.12. The
  `Dockerfile` is now pinned to
  `python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203`,
  matching the digest format the deploy-hardening test enforces and restoring a
  deterministic, wheel-only image build.

- **2026-06-02 (2.5.58):** Cleared the dashboard security-audit gate by moving
  `PyJWT[crypto]` to `2.13.0` and regenerating the uv lockfile. Queue mobile
  coverage now asserts the component's current `Queue health summary`
  accessible label so Vitest tracks the shipped UI contract.

- **2026-06-02 (2.5.55):** Legacy dashboard accessibility and theme-token
  hygiene are hardened. `frontend/src/legacy/App.tsx` now exposes sub-tabs as a
  labelled ARIA tablist with roving keyboard focus, keeps sortable table headers
  on their native `columnheader` role while preserving keyboard sorting, labels
  the dispatch modal as an `aria-modal` dialog, and routes legacy issue/status
  badge colors through semantic theme tokens. `frontend/src/pages/Fleet/LabelGuide.tsx`
  aligns its fallback colors with the dashboard token contract, and
  `tests/frontend/test_color_literal_budget.py` tracks the remaining literal
  budget so legacy color debt cannot silently grow. `tests/e2e/a11y.spec.ts`
  adds Playwright + axe coverage for critical pages, with `@axe-core/playwright`
  recorded in the frontend dev dependencies.
- **2026-06-02 (2.5.54):** The dashboard design system now supports a broader
  fleet theme set plus density-aware elevation polish. `frontend/src/design`
  adds six additional accessible palettes, exports radius/elevation/shadow
  tokens, and verifies theme contrast and token behavior with dedicated tests.
  `frontend/src/hooks/useDensity.ts` and `frontend/src/components/DensityToggle.tsx`
  provide a persisted compact/comfortable density control, with tests covering
  storage and UI behavior. `frontend/src/index.css` and `frontend/src/main.tsx`
  apply the tokens globally so table, card, control, and shell surfaces share a
  single DRY visual contract. The frontend color-literal budget is intentionally
  raised to account for the new shadow/elevation token inventory.
- **2026-06-01 (2.5.53):** Hardened runner busy-detection against the recurring
  "autoscaler/cleanup stops a busy runner mid-job" regression (Runner_Dashboard#640;
  observed 2026-06-01 cancelling PR #813 `docker-build-scan` mid OCI-export, #814
  `tests`, #815 `security-scan`). Three independent gaps were closed. (1)
  `backend/autoscaler_busy.py` adds **Strategy 3b** — `_runner_busy_via_worker_scan`,
  a global `Runner.Worker` scan keyed on the runner's `WorkingDirectory`. The
  MainPID child-walk (Strategy 3) returns a false negative when the Worker has
  reparented (`KillMode=mixed`) or systemd's MainPID transiently points only at the
  listener; the pickup-dir mtime (Strategy 1) goes stale during a long step that
  emits no file-commands (e.g. a ~10-min `docker buildx` export). A live Worker is
  ground truth, so `_runner_is_busy` now falls through to 3b instead of concluding
  "idle" after an empty child-walk, and also consults 3b in the MainPID=0 window
  before the coarse ActiveState fallback. (2) The lockfile signal (Strategy 2) was
  dead fleet-wide after any reboot: `/run` is tmpfs and the runner user cannot
  `mkdir` under root-owned `/run`, so `/run/runner-busy` vanished and the JOB_STARTED
  hook's sentinel was never written. New `deploy/tmpfiles.d/runner-busy.conf` (wired
  into `deploy/install-runner-maintenance.sh`) recreates the dir as root on every
  boot. (3) `deploy/runner-cleanup.sh` looked up the lockfile by the **workdir
  basename** (`runner-4.lock`) while the hook writes the **registered runner name**
  (`d-sorg-…-nvme-4.lock`) — a guaranteed miss; cleanup now derives the registered
  name from the unit via `runner_name_for_unit`. New regression tests:
  `TestRunnerIsBusyWorkerScan` (3) in `tests/test_runner_autoscaler.py` and
  `TestBusyViaWorkerScan` (5) in `tests/test_autoscaler_busy.py`.
- **2026-05-31 (2.5.52):** Modern shell — default desktop layout, reversible
  (epic #796 / issue #802). `frontend/src/shell/DesktopShell.tsx` composes the
  three merged surfaces into a GitHub-style desktop layout, all driven by the
  one nav registry (DRY): the left `Sidebar` (#798), the slim `TopToolstrip`
  (#799), and the `Tooltip` primitive (#801) on every nav item and on every
  shell action button. Navigation is flat typed props (LoD): `activeTabId`,
  `onSelect(tabId)`, and a flat `ShellAction[]` (each with a required non-empty
  `tooltip`, enforced by `assertActions` — DbC). The page body is mounted in the
  `main` landmark, isolated from the nav chrome (orthogonality — a failing page
  cannot remove navigation). The legacy `App` is now mountable _chromeless_ and
  _tab-controlled_ (new optional `activeTab` + `chromeless` props): the shell
  owns navigation while App renders only the requested page body, so every
  existing page renders unchanged. `frontend/src/shell/layoutFlag.ts`
  (`resolveDesktopShellLayout`) makes the modern shell the DEFAULT desktop layout
  while keeping it fully reversible: `localStorage["dashboard.layout"]="legacy"`
  (a visible "Classic layout" action button) or `VITE_DESKTOP_SHELL` opt-out
  pins the untouched legacy shell. `frontend/src/main.tsx` selects the layout on
  desktop. Every interactive control in the shell carries an accessible label or
  tooltip — an a11y audit test asserts no `button`/`a[href]`/`role=button`/
  `role=menuitem` lacks an accessible name. TDD: 10 `DesktopShell` behaviour +
  audit tests and 6 `layoutFlag` tests. `vite build`, ESLint, and Vitest (391
  tests) pass.

- **2026-05-31 (2.5.51):** Modern shell — slim top toolstrip (epic #796 / issue
  #799). `frontend/src/shell/TopToolstrip.tsx` replaces the legacy ~24-button
  toolstrip with just the registry's most-frequent categories (Fleet, Queue,
  Remediation, Conductor — those with `frequent: true`) as direct buttons;
  every other category collapses into a single accessible grouped "More"
  `Dropdown` (#800), ordered by nav group. Rendered entirely from the nav
  registry (DRY): the frequent buttons come from `frequentItems()` and the
  overflow from the non-frequent remainder, so the toolstrip and sidebar never
  drift. Each frequent button is wrapped in the `Tooltip` primitive (#801)
  (hover + focus, `aria-describedby`) and the active category is marked
  `aria-current="page"`; when the active tab lives in the overflow menu, the
  "More" trigger itself carries `aria-current` (new optional `triggerActive`
  prop on the `Dropdown` primitive). It is a labelled WAI-ARIA `toolbar` that
  wraps responsively (`flex-wrap`); below 900px the text labels hide to icons
  via `.slim-toolstrip__label`. LoD: flat `activeTabId` + `onSelect(tabId)`
  props. Orthogonality: pure presentational nav, owns no page state. Exported
  from `frontend/src/shell/index.ts`. 14 behaviour tests for the toolstrip plus
  2 for the new `Dropdown.triggerActive` prop. Mounted as the default desktop
  shell behind a layout flag in #802.

- **2026-05-31 (2.5.50):** Modern shell — left Sidebar (epic #796 / issue #798).
  `frontend/src/shell/Sidebar.tsx` is a GitHub-style left navigation rendered
  entirely from the nav registry (DRY): one collapsible section per group, every
  category as a nav button with the registry tooltip as its accessible `title`.
  Active item is marked `aria-current="page"`. Per-group collapse and a
  whole-sidebar icon-rail collapse both persist to `localStorage`
  (`dashboard.sidebar.collapsedGroups`, `dashboard.sidebar.railCollapsed`).
  Roving ArrowUp/Down keyboard navigation across visible items; the sidebar is a
  labelled `navigation` landmark. It is purely presentational — flat
  `activeTabId` + `onSelect(tabId)` props (LoD) — so a failing page cannot break
  it (orthogonality). 10 behaviour tests. Not yet mounted in the desktop shell;
  that is wired behind a layout flag in #802.

- **2026-05-31 (2.5.49):** Modern shell — Tooltip + Dropdown primitives (epic
  #796 / issues #801, #800). `frontend/src/primitives/Tooltip.tsx` is a reusable
  accessible tooltip: shows on hover **or** keyboard focus after a small delay,
  associates with its trigger via `aria-describedby`, and dismisses on
  mouseleave / blur / Escape. It composes onto the child's own event handlers
  (LoD) and renders nothing for blank content.
  `frontend/src/primitives/Dropdown.tsx` is a reusable WAI-ARIA menu-button
  dropdown (`aria-haspopup="menu"` / `aria-expanded`, `role="menu"` /
  `menuitem`): ArrowUp/Down roving focus with wrap, Enter/Space activate,
  Escape closes and restores trigger focus, click-outside closes. Both are
  exported from `frontend/src/primitives/index.ts` and back every nav item and
  action-button hover hint in the new shell (#802). 16 behaviour tests.
- **2026-05-31 (2.5.48):** Modern shell — nav registry (epic #796 / issue #797).
  `frontend/src/shell/navRegistry.ts` is now the single typed source of truth
  for every navigable category: each `NavItem` carries `{ id, label, group,
Icon, tooltip, tabId, frequent }`. Six ordered groups (Fleet & Runners,
  Workflows & Jobs, Orchestration, AI & Agents, Reports & Analysis, Admin &
  Settings) bucket all categories. `assertValidNavRegistry()` enforces the
  Design-by-Contract invariants (unique ids/tabIds, non-empty tooltips, valid
  group refs, every group non-empty, at least one — but not all — `frequent`)
  and runs at module load so a malformed registry fails fast. Selectors
  `frequentItems()`, `itemsByGroup()`, and `navItemById()` keep view code
  declarative (LoD). Shared inline SVG icons live in
  `frontend/src/shell/navIcons.tsx`. The sidebar (#798), slim toolstrip (#799),
  and grouped dropdowns (#800) all render from this one registry (DRY).
- **2026-05-31 (2.5.47):** Conductor dashboard integration — the admission-gate
  - visibility surface the Conductor orchestrator (in `Repository_Management`,
    epic #1273 / issue #1282) calls **over HTTP** to obtain CI dispatch slots and
    to surface its tracked work. New module `backend/orchestrator_api.py`
    registers four routes under `/api/orchestrator`:
    `POST /lease` (admission gate — grants a dispatch slot when idle runners minus
    a caller-supplied `reserve` still cover the requested `slots` **and** the queue
    is in `running` mode; returns `granted=false` with a reason under saturation or
    a manual pause/drain — this is the backpressure the orchestrator obeys, and
    consumes capacity via a TTL lease, default 900 s, reaped lazily so a crashed
    orchestrator cannot wedge the fleet), `POST /release` (frees a lease,
    idempotent for unknown ids), `GET /queue` (mode, active leases, capacity,
    work classification, provider mix, budget burn) and `POST /queue` (manual
    pause / resume / drain). Capacity reuses the same arithmetic as
    `/api/runners/fleet/capacity` via the new shared
    `routers.runner_helpers.count_runner_capacity` helper (DRY); the fleet capacity
    route was refactored onto it. DbC: pydantic request/response models with field
    constraints + validators on every POST; the gate fails safe (denies) when the
    capacity source is unwired. The whole surface is gated behind the
    `DASHBOARD_ENABLE_CONDUCTOR` env flag (default off) — every route returns 404
    and the frontend tab is hidden until enabled (reversible, orthogonal). A new
    **Conductor tab** (`frontend/src/pages/Conductor.tsx`) shows planned/active/
    blocked work, provider mix, budget burn, and pause/resume/drain controls; it
    probes the endpoint once and only appears when the flag is on. Its visible
    state badges, disabled/error states, and pause/resume/drain controls use the
    shared `Badge`, `EmptyState`, and `TouchButton` primitives with scoped
    `conductor__*` token classes. 15 backend route
    tests in `tests/api/test_orchestrator_api.py` and 6 frontend behaviour tests in
    `frontend/src/pages/__tests__/Conductor.test.tsx`. **Conductor (RM) side must:**
    call `POST /api/orchestrator/lease` before dispatching and obey `granted=false`;
    send the CSRF sentinel header `X-Requested-With: XMLHttpRequest` on all POSTs;
    `POST /api/orchestrator/release` when work completes; optional `provider` field
    on the lease feeds the provider mix. Optional env: `CONDUCTOR_LEASE_TTL_SECONDS`,
    `CONDUCTOR_DEFAULT_RESERVE`, `CONDUCTOR_BUDGET_LIMIT_USD`.
- **2026-05-30 (2.5.46):** `/api/stats` (Overview summary) no longer publishes
  false zeros under partial GitHub failure. Previously the org PR/issue search,
  the 24-repo queue fan-out, and the fleet probe shared one timeout budget, so a
  slow search or secondary rate-limit timed out the whole bundle and zeroed PRs,
  queue, and machine counts together — even while the standalone `/api/queue`
  (toolstrip) stayed correct. `backend/routers/repos_stats.py` now: (1) reuses
  the resilient `/api/queue` cache instead of re-fanning-out, (2) budgets
  runners, workflow-runs, PR search, issue search, and fleet **independently**,
  (3) backfills any failed field from a `stats:stale` last-known-good snapshot
  (24h TTL) that is only written on a fully-healthy compute, and (4) adds a
  `stale` flag to the payload. 4 new tests in
  `tests/test_stats_summary_resilience.py`.
- **2026-05-30 (2.5.45):** Queue reaper now detects **unroutable** queued runs.
  `backend/queue_cleanup.py` adds `StaleReason.UNROUTABLE_LABEL` plus
  `is_routable()`, `fetch_online_runner_label_sets()`, and
  `required_labels_for_run()`. `find_stale_runs` fetches the online-runner label
  inventory once per scan; `_queued_stale_for_repo` flags any queued run past the
  age gate whose `runs-on` labels are a superset of no online runner's labels
  (e.g. a removed/renamed tier like `d-sorg-fleet-16core`). These are
  `safe_to_cancel=True` because they will never start regardless of age. The
  check fails safe: an empty/unavailable runner inventory (transient API error)
  or missing job metadata never flags a run, so no false-positive cancellations.
  12 new unit tests in `tests/test_queue_cleanup_unroutable.py`.
- **2026-05-30 (2.5.45):** Hub circuit breaker so a dead hub never blanks a spoke's
  dashboard. Previously a `node` (spoke) proxied every fleet-wide endpoint
  (`/api/runners`, `/api/runs`, `/api/queue`, `/api/stats`, `/api/fleet/nodes`) to
  `HUB_URL`, and if the hub was unreachable `proxy_to_hub` raised 504/503 on every
  request — so an offline hub rendered the whole dashboard blank fleet-wide. A hub
  timeout/connect-error now opens a short-lived circuit breaker
  (`mark_hub_unreachable` / `hub_in_cooldown` / `reset_hub_circuit` in
  `backend/proxy_utils.py`, default 30s); while it is open `should_proxy_fleet_to_hub`
  (both `backend/proxy_utils.py` and `backend/server.py`) returns False so the node
  serves its OWN local data instead. A successful proxy closes the breaker
  immediately. Tests in `tests/test_proxy_utils.py::TestHubCircuitBreaker`.
- **2026-05-29 (2.5.44):** Keep the WSL distro resident between keepalive probes
  so the idle runner fleet stays online. A WSL2 distro is torn down a few seconds
  after the last active `wsl.exe` session ends; in-distro systemd services
  (including `wsl-runner-keepalive.service`'s `sleep 600`) do not keep it alive,
  and the watchdog's periodic probe attaches/detaches each cycle, so an idle host
  let the distro terminate between probes and dropped every runner offline.
  `deploy/wsl-keepalive.ps1` now maintains exactly one persistent
  `wsl --exec /bin/sleep infinity` session (new `Set-DistroPin` helper, called
  every loop and restarted if a shutdown kills it). Complements #784 (correct
  probe) and #783 (S4U task survives logoff; docker boot decouple) — neither
  keeps the distro resident between probes. New static regression test
  `test_script_pins_distro_with_persistent_session`.
- **2026-05-29 (2.5.43):** Hardened autoscaler scale-down against undeployed
  runner drain drop-ins (issue #785). Before `backend/autoscaler_systemd.py`
  calls `systemctl stop` for any `actions.runner.*` unit, it now verifies the
  effective systemd stop contract reports `KillMode=mixed` and
  `TimeoutStopUSec >= 120s`. If a host has the code but not the active
  #640/#679 drop-in, the autoscaler refuses to stop the runner and logs the
  deploy action instead of risking a mid-job shutdown. Focused tests cover the
  systemd time-span parser, safe/unsafe contract decisions, and the stop guard.

- **2026-05-28 (2.5.40):** Storage-tier aware disk pressure metrics and classification (issue #754).
  `backend/system_utils.py` gains two new pure helpers:
  `get_host_disk_for_pool(pool)` — derives the correct Windows host drive mount path from pool registry
  metadata (`storage.host_drive` preferred, falling back to the drive letter embedded in `storage.vhdx_path`),
  correcting the prior hard-coded `/mnt/c` assumption that caused the D: HDD-backed VHDX incident to go
  detected; and `classify_disk_pressure_by_tier(storage_tier, percent, free_gb, io_pressure_full_avg10)` —
  returns a four-level pressure status (`low` / `medium` / `high` / `critical`) and `binding_constraint`
  (`io` / `capacity` / `none`) using tier-specific thresholds: NVMe pools treat IO saturation
  (`io_pressure_full_avg10 >= 50`) as the primary constraint, while HDD/SSD pools treat capacity as
  primary and limit IO-only escalation to `medium`. A new `GET /api/disk/pool-pressure` endpoint
  in `backend/metrics.py` iterates all pools in `machine_registry.yml`, measures live disk usage at
  each pool's `runner_base_dir`, and returns per-pool pressure reports with tier, backing disk path,
  VHDX path, bus type, and capacity/IO metrics. Tests in `tests/test_system_utils.py` and
  `tests/api/test_pool_disk_pressure.py` cover tier classification, host-disk path resolution,
  and the endpoint response shape.

- **2026-05-28 (2.5.40):** Added operator diagnostics for VHDX compaction, WSL attach failures, and pool recovery (issue #756). New `GET /api/diagnostics/vhdx` endpoint exposes VHDX attachment status for all WSL distributions via `Get-DiskImage` (powershell.exe), including sharing-violation (ERROR_SHARING_VIOLATION) detection. New `GET /api/diagnostics/pool-recovery` endpoint returns structured recovery guidance for `vhdx_locked`, `disk_full`, and `wsl_boot_failure` scenarios — each with operator action steps and safety warnings (notably: do not restart WSL during active Optimize-VHD compaction). Both endpoints are implemented in `backend/routers/diagnostics.py` and tested in `tests/api/test_pool_diagnostics.py`.

- **2026-05-28 (2.5.40):** Added workflow routing guidance API for issue #757.
  `backend/routers/label_guidance.py` exposes two new endpoints:
  `GET /api/runners/label-guidance` returns per-label workload guidance, copy-paste
  `runs-on` snippets, and the full workflow-class taxonomy sourced from
  `config/workflow_runner_routing_policy.json`.
  `GET /api/runners/label-audit` runs the offline policy audit (no WSL required)
  and returns structured violations and migration recommendations.
  `frontend/src/pages/Fleet/LabelGuide.tsx` adds a Label Guide section to the
  Fleet tab with a taxonomy table and copy buttons.
  `docs/runner-labels.md` is the canonical label documentation.
  Tests in `tests/api/test_label_guidance.py` and
  `frontend/src/pages/Fleet/__tests__/LabelGuide.test.tsx` pin the contracts.

- **2026-05-28 (2.5.40):** Added tier-aware autoscaler controls for ControlTower
  NVMe and HDD pools (issue #755). New `backend/routers/autoscaler_pools.py`
  exposes two endpoints:

  - `GET /api/autoscaler/pools` — returns per-pool scaling state (pool name,
    min/max/default online counts, systemd unit pattern, labels, start/stop
    enabled flags, primary pressure metric name, cooldown secs, dry_run flag).
    Response shape: `{pools: PoolScalingState[], cooldown_secs: int, dry_run: bool}`.
  - `POST /api/autoscaler/pools/{pool}/config` — runtime override for a pool's
    `min_online` / `max_online` counts. Applies by writing env vars
    (`AUTOSCALER_{POOL}_MIN_ONLINE`, `AUTOSCALER_{POOL}_MAX_ONLINE`) into the
    current process; a service reload propagates to the autoscaler loop. Accepts
    `{min_online: int (>=0), max_online: int (>=1)}`. Returns 422 for unknown
    pool names or when `min_online > max_online`. DbC postconditions assert env
    vars were written. Existing autoscaler config constants (NVME*\* / HDD*\*
    family) and `_get_pool_config` / `_classify_unit` pool dispatch logic remain
    the single source of truth for pool parameters; the router only reads and
    exposes them.
    New tests: `tests/api/test_autoscaler_pools.py` (17 tests).

- **2026-05-28 (2.5.40):** Added multi-pool backend aggregation to
  `GET /api/fleet/status` (issue #753). When the dashboard runs on port 8321
  (ControlTower-NVMe), it automatically queries its peer on port 8322
  (ControlTower-HDD) and merges both pool responses into a single JSON object
  keyed by pool name. If the peer is unreachable the endpoint returns an
  `"offline"` entry for that pool rather than failing the whole request,
  preserving orthogonality. Pass `?exclude_pools=true` to suppress the peer
  query (used internally so the peer does not recurse back). The Fleet tab
  `Mobile.tsx` already renders ControlTower pool nodes in a dedicated
  side-by-side section when their names start with `"ControlTower"`. Tests in
  `tests/api/test_fleet_aggregator.py` cover the full-aggregate, peer-offline
  fallback, and exclude_pools code paths.

- **2026-05-27 (2.5.39):** Optimized per-runner worker process resource metric collection in `backend/routers/system.py` and `backend/system_utils.py` by pre-computing path patterns and optimizing the process iteration loop to avoid filesystem lookup overhead, preventing CI Standard timeouts. Updated `.github/workflows/ci-standard.yml` to exempt newly expanded files from the 500-line check soft cap, and disabled autoderiving fleet nodes in tests (`tests/conftest.py`) to prevent network timeouts.
- **2026-05-26 (2.5.37):** Fixed pre-existing TestErrorHandling test pollution in `tests/api/test_routers_runners.py`. Earlier tests in `TestGetRunners` populate two pieces of module-level state — `cache_utils._cache` (TTL cache) and `runners_router._last_successful_runners` (degraded-mode fallback). Once populated, the API-error / rate-limit tests in `TestErrorHandling` received `source='cache'` instead of `'unavailable'`/`429`, because the endpoint falls back to the last-known-good response when the mocked GitHub call fails. The actual root cause was the global, not just the cache. Added an autouse fixture on `TestErrorHandling` that clears both pieces of state before and after each test. 33/33 passing locally (was 31 pass + 2 fail).
- **2026-05-26 (2.5.36):** Comprehensive autoscaler-corruption recovery + VS Code blank-window fix. New `backend/runner_state_cleanup.py` module removes orphaned `$HOME/.gitconfig.lock` (and stale per-worktree git locks older than 60s) after every autoscaler stop — recovering from the fleet-wide outage signature where a SIGTERM mid-`git config --global` poisons every subsequent `actions/checkout` on the host (Runner_Dashboard#640). Cleanup is invoked from `_stop_unit` on every stop path and mirrored in `deploy/runner-hooks/job-started.sh`. `backend/routers/credentials.py` Cline detection uses a new `_resolve_vscode_cli` helper that prefers `code.cmd` over `Code.exe` on Windows and a `_vscode_has_extension` helper that passes `CREATE_NO_WINDOW`. 7 new unit tests + 3 updated; 25/25 passing.
- **2026-05-26 (2.5.35):** Added the ControlTower workflow-routing policy
  contract for issue `#757`. `config/workflow_runner_routing_policy.json`
  now defines the future `d-sorg-fleet-bulk`, `d-sorg-fleet-fast-io`,
  `d-sorg-fleet-docker`, and `d-sorg-fleet-nvme` label taxonomy, while
  `scripts/check_workflow_runner_routing.py` performs an offline audit that
  fails only on explicit tier misuse and reports neutral-label migration
  recommendations. `docs/runbooks/runner-routing-labels.md` is the operator
  guide for the transition from today's neutral `d-sorg-fleet` label to the
  planned dual-tier NVMe/HDD host.
- **2026-05-26 (2.5.35):** Hardened split-disk/NVMe dashboard deployments.
  `deploy/setup.sh` now accepts `--runner-base-dir` and `--deploy-dir`, installs
  locked runtime dependencies into the deployed `.venv`, and templates
  `RUNNER_BASE_DIR` into the systemd unit so dashboard runner controls can point
  at non-default runner roots. The dashboard service now runs as `Type=simple`,
  tolerates mirrored-port restore failures, disables the unavailable sd_notify
  watchdog path, and collects crash diagnostics through
  `deploy/collect-crash-diagnostics.sh` on stop/failure. The Windows WSL
  keepalive script and installer support a no-reset resident mode for keeping
  the selected distro warm without restarting all WSL instances. The diagnostics
  and runner APIs report degraded fleet-node state instead of failing requests
  when remote runner hosts are unhealthy.
- **2026-05-26 (2.5.35):** Clarified the `/api/health` contract after the
  GitHub admin helper signature repair. Health polling now calls the shared
  GitHub runner-list helper with its supported endpoint-only signature instead
  of passing an unsupported timeout override. The endpoint continues to report
  dashboard status plus GitHub connectivity fields such as
  `github_api`, `github_check_seconds`, and `runners_registered`, and focused
  tests pin both the helper-call contract and the API response shape.

- **2026-05-25 (2.5.34):** Restored the Docker runtime image to the pinned
  Python 3.12 base required by `pyproject.toml`, deployment hardening tests,
  and the lockfile's binary wheel availability. This prevents Python 3.14
  source builds for packages such as `pydantic-core`, `uvloop`, and
  `watchfiles`, reducing CI I/O pressure and making Docker validation
  deterministic again.

- **2026-05-25 (2.5.33):** Hardened runner-autoscaler recovery after WSL I/O
  pressure incidents. The autoscaler now protects a default two-runner online
  floor, respects that floor even when scheduler desired capacity drops lower,
  and restores a configurable four-runner recovery pool after overload clears
  before waiting for full low-pressure recovery to rebuild the entire scheduled
  pool. `AUTOSCALER_RECOVERY_MIN_ONLINE` exposes the recovery pool size, and
  deploy/test coverage pins the default floor contract. Fleet deployment now
  disables boot-time autostart for `actions.runner.*` units and makes bulk
  runner restarts opt-in, so WSL restarts do not launch every runner before
  the autoscaler can sample host pressure.

- **2026-05-25 (2.5.32):** Hardened autoscaler overload detection for
  ControlTower WSL I/O stalls. `backend/autoscaler_sampling.py` now parses
  Linux PSI data from `/proc/pressure/io`, and `backend/runner_autoscaler.py`
  treats high `full avg10` I/O pressure as overload while blocking scale-up
  recovery until I/O pressure drops. `AUTOSCALER_IO_PRESSURE_FULL_HIGH`,
  `AUTOSCALER_IO_PRESSURE_FULL_LOW`, and
  `AUTOSCALER_ACTION_COOLDOWN_SECONDS` make the thresholds configurable.
  Focused tests cover the PSI parser and exported autoscaler contract.

- **2026-05-25 (2.5.31):** Fixed tautological rgba() budget assertion in
  `tests/frontend/test_color_literal_budget.py`. The test previously recalculated
  `BUDGET = count + 20` on every run, making `count <= count + 20` always true.
  Changed to a hard-coded constant `RGBA_BUDGET = 73` (current baseline established
  at D5 landing) so the test now enforces a real regression gate. Part of issue #706
  (Epic: Dashboard Hardening & UX Overhaul), subtask D5.

- **2026-05-24 (2.5.30):** Hardened autoscaler config parsing and module-level
  test coverage for issue `#727`. `backend/autoscaler_config.py` now raises
  `ValueError` at startup when autoscaler numeric env vars are malformed or
  negative instead of silently falling back to defaults. Focused tests now pin
  that contract plus the autoscaler sampling/systemd helpers, and the targeted
  autoscaler coverage lane measures `autoscaler_busy=89%`,
  `autoscaler_config=92%`, `autoscaler_sampling=92%`, and
  `autoscaler_systemd=98%`.

- **2026-05-24 (2.5.30):** Hardened the Windows WSL keepalive watchdog for
  runner-dashboard recovery. `deploy/wsl-keepalive.ps1` now accepts
  `DashboardPort` and `DashboardServiceName`, validates both inputs, probes the
  local `/health` endpoint, attempts a dashboard-only `systemctl start` when
  WSL is responsive but the dashboard is unhealthy, and escalates to a full
  WSL reset only if that targeted recovery still fails. The script logs the
  new `dashboard_unhealthy_detected`, `dashboard_recovery_started`,
  `dashboard_recovery_failed`, and `dashboard_recovery_after_wsl_reset_*`
  events, and `tests/deploy/test_wsl_keepalive_script.py` asserts the new
  parameter validation and recovery-path coverage.

- **2026-05-23 (2.5.29):** Documented A-series infrastructure hardening:
  Prometheus autoscaler and lease-reaper metrics, `/readyz` runner-health
  probing, quick-dispatch health gating/backpressure, drain-mode deployment
  controls, runner memory/restart systemd drop-ins, deployment preflight
  checks, and the autoscaler Grafana dashboard.

- **2026-05-23 (2.5.28):** Added B-series API contract hardening:
  `backend/gh_client.py` now owns GitHub API retries, timeouts, and HTTP error
  normalization; backend code may not shell out to `gh api` for request paths.
  Request payloads for help chat and launcher generation are validated by
  Pydantic models in `backend/models/requests.py`, and API errors use the
  shared `backend/error_models.py` envelope with structured audit coverage
  while preserving structured HTTP exception details and response headers such
  as `Retry-After`.
  The anti-phantom issue-resolution guard now recognizes this repo's real
  implementation roots (`backend/` and `frontend/src/`) when validating
  feature PRs and issue path evidence.
- **2026-05-22 (2.5.27):** Added relative-timestamp primitive (#725):
  `frontend/src/hooks/useTimeAgo.ts` (`useTimeAgo`, `formatTimeAgo`) and
  `frontend/src/primitives/TimeAgo.tsx` (`<TimeAgo iso={...} />`). Renders
  semantic `<time>` with the raw ISO in `dateTime` + tooltip; future
  timestamps render `"soon"`, invalid input degrades to the raw value with
  a `console.warn`. Wired into `Reports/Mobile.tsx` "Modified" field as the
  first call-site; remaining pages migrate in follow-ups.
- **2026-05-22 (2.5.26):** Added `DASHBOARD_HOST` env var
  (`dashboard_config.HOST`) for uvicorn bind interface; default preserves
  historical `0.0.0.0` behaviour. Added `deploy/wsl-mirrored-port-helper.sh`
  invoked from the systemd unit's `ExecStartPre`/`ExecStartPost` to dodge
  the recurring WSL-mirrored Tailscale-serve port conflict that crash-looped
  the dashboard after every WSL cold-restart. Added `deploy/wsl-keepalive.ps1`
  Windows watchdog with responsiveness probe, structured JSONL logging, and
  exponential backoff. Added host-wide Windows resource metrics for WSL
  dashboard telemetry and autoscaler decisions so runner scaling uses the same
  CPU/RAM values operators see in Task Manager. See
  `docs/wsl-mirrored-port-conflict.md`.

---

## 0. Sibling repos & boundaries

`runner-dashboard` is one of three repos that together form the
D-sorganization fleet operating system. The cross-repo contract is
documented canonically in
[`Repository_Management/docs/sibling-repos.md`](https://github.com/D-sorganization/Repository_Management/blob/main/docs/sibling-repos.md).
Quick form:

- **[`Repository_Management`](https://github.com/D-sorganization/Repository_Management)** â€” fleet orchestrator.
  Publishes shared CI workflows, skills, templates, agent coordination.
  _Does not_ own dashboard UI, backend, or HTTP API.
- **`runner-dashboard`** (this repo) â€” operator console. Owns every dashboard
  tab, every `/api/*` endpoint, deployment + rollback machinery.
- **[`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon)** â€”
  autonomous local AI control plane. The Maxwell tab here calls the daemon
  over HTTP; the daemon never calls back.

This SPEC documents only what `runner-dashboard` owns. Fleet-wide workflow
manifests, agent claim/lease protocol, Project_Template, and skill publishing
specs live in [`Repository_Management/SPEC.md`](https://github.com/D-sorganization/Repository_Management/blob/main/SPEC.md).
The Maxwell pipeline state machine and ExecutionSandbox specs live in
[`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon).

---

## 1. Purpose and Scope

The Runner Dashboard is the central web UI control surface for the
D-sorganization self-hosted GitHub Actions runner fleet. It aggregates runner
health, workflow activity, AI agent dispatch, and fleet operations into a
single browser-based interface backed by a local FastAPI server.

**In scope:**

- Real-time monitoring of all self-hosted runners and their systemd services
- Workflow run history, queue management, and cancellation/rerun controls
- AI agent dispatch (Jules, GAAI, Claude, Codex, Maxwell) via remediation API
- Multi-node fleet hardware and system metrics
- Scheduled workflow inventory and manual dispatch
- Local application process health monitoring
- Credential management and secrets inventory
- Automated runner scaling configuration
- Fleet orchestration and cross-node deployment control

**Out of scope:**

- GitHub-hosted runner management (cloud runners)
- Repository code review or merge operations (delegated to agent workflows)
- Direct SSH access to fleet nodes (use the deploy scripts)

---

## 2. Architecture

### 2.1 Backend

**Runtime:** Python 3.11+
**Framework:** FastAPI (ASGI via uvicorn)
**Port:** 8321 (configurable via `DASHBOARD_PORT` env var)
**Entry point:** `backend/server.py`

The backend is a single-process FastAPI application that:

1. Proxies the GitHub REST API (runners, workflows, runs, repos) using an
   authenticated `httpx.AsyncClient` with the `GITHUB_TOKEN` environment variable.
2. Controls local systemd runner services (`systemctl start/stop`) via
   subprocess calls when running in WSL/Linux.
3. Collects real-time system metrics (CPU, RAM, disk, GPU/VRAM) using `psutil`
   and vendor-specific CLI tools.
4. Reads and writes runtime configuration files (YAML/JSON) from `config/` and
   `~/.config/runner-dashboard/`.
5. Serves the built frontend SPA (`dist/index.html`) as a static file at `GET /`.

Runtime configuration files include the optional `config/linear.json`, which
declares Linear workspaces, team filters, and taxonomy mappings used by the
Linear and unified issue inventory endpoints.

When the backend runs as a Windows fallback process, Linux-only probes must not
raise request-time exceptions. `/api/system` returns Windows-safe `psutil`
metrics, systemd keepalive checks report `unsupported` with an explanatory
detail, scheduler timers report inactive instead of shelling out to
`systemctl`, and `.wslconfig` discovery checks native Windows profile paths.
The Windows Scheduled Task keepalive probe must execute valid PowerShell and
surface task action details without exposing secrets.

Local hook and CI checks must be runnable from Windows development hosts
without bypass flags. Tests that require POSIX-only capabilities such as
`fcntl`, symlink creation privileges, POSIX mode-bit enforcement, or a healthy
WSL/bash service must skip only when that capability is unavailable locally and
must remain active on Linux CI. Workflow guards that inspect PR file lists must
use GitHub API file pagination rather than `gh pr diff --name-only`, because
the fleet runner CLI version does not support that flag consistently.

**Supporting modules (all in `backend/`):**

| Module                    | Responsibility                                                                                                                                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_remediation.py`    | AI agent dispatch plans, Jules/GAAI/Claude invocation                                                                                                                                                                     |
| `dispatch_contract.py`    | Type contracts for workflow dispatch payloads                                                                                                                                                                             |
| `machine_registry.py`     | Multi-node fleet registry (load, merge with live data)                                                                                                                                                                    |
| `scheduled_workflows.py`  | Inventory of scheduled workflow definitions                                                                                                                                                                               |
| `deployment_drift.py`     | Version drift detection between deployed and expected                                                                                                                                                                     |
| `local_app_monitoring.py` | Health checks for local process/app registry                                                                                                                                                                              |
| `usage_monitoring.py`     | Per-runner CPU/RAM usage time-series collection                                                                                                                                                                           |
| `workflow_stats.py`       | Aggregate workflow success/failure statistics                                                                                                                                                                             |
| `report_files.py`         | Parse dated report files for the Reports tab                                                                                                                                                                              |
| `runner_autoscaler.py`    | Dynamic runner count scaling — main loop and public re-export facade                                                                                                                                                      |
| `autoscaler_config.py`    | Autoscaler env-var helpers and all threshold constants, including I/O pressure gates                                                                                                                                      |
| `autoscaler_systemd.py`   | Autoscaler systemd unit enumeration, state inspection, start/stop                                                                                                                                                         |
| `autoscaler_busy.py`      | Autoscaler layered busy-detection (4 strategies, issue #651)                                                                                                                                                              |
| `autoscaler_sampling.py`  | Autoscaler resource sampling (CPU/mem/disk/load/I/O pressure) and scheduler integration                                                                                                                                   |
| `config_schema.py`        | Config validation and atomic JSON writes                                                                                                                                                                                  |
| `pr_inventory.py`         | Fetch and normalise open PRs across repos (issue #80)                                                                                                                                                                     |
| `issue_inventory.py`      | Fetch and normalise open issues with taxonomy (issue #81)                                                                                                                                                                 |
| `linear_inventory.py`     | Fetch and normalise Linear issues into the canonical issue inventory shape                                                                                                                                                |
| `health.py`               | Health check endpoints (`/api/health`, `/health`) extracted from server.py (issue #159)                                                                                                                                   |
| `metrics.py`              | System metrics endpoints (`/api/system`, `/api/fleet/status`) extracted from server.py (issue #159)                                                                                                                       |
| `fleet_events.py`         | Fleet event log + alarm feed (issue #863): typed `FleetEvent`, pure `classify_fleet_events` (offline/online/low-disk/saturation/watchdog), bounded `EventStore` ring buffer, and `FleetEventPoller` driving `/api/events` |

**Bounded domain routers (`backend/routers/`):**

Well-bounded API domains with no cross-domain shared state are extracted into
`APIRouter` modules and registered with `app.include_router()`. This reduces
coupling and makes each domain independently testable.

| Router                   | Prefix                | Responsibility                                                                       |
| ------------------------ | --------------------- | ------------------------------------------------------------------------------------ |
| `routers/deployment.py`  | `/api/deployment`     | Deployment metadata, expected-version, drift, git-drift (issue #357)                 |
| `routers/reports.py`     | `/api/reports`        | Report file listing and dated metric parsing (issue #358)                            |
| `routers/heavy_tests.py` | `/api/heavy-tests`    | Heavy test run tracking and result storage (issue #358)                              |
| `routers/assessments.py` | `/api/assessments`    | Repo assessment JSON listing and retrieval (issue #358)                              |
| `routers/dispatch.py`    | `/api/fleet/dispatch` | Fleet agent dispatcher â€” allowlisted hub-to-node commands                          |
| `routers/credentials.py` | `/api`                | Credential probe â€” tool/key presence without exposing values                       |
| `routers/linear.py`      | `/api/linear`         | Optional Linear read API for workspaces, teams, and issue inventory                  |
| `push.py`                | `/api/push`           | Web Push subscription storage, scoped unsubscribe, and test-send foundation          |
| `routers/events.py`      | `/api/events`         | Read-only fleet event-log feed backed by the `fleet_events` ring buffer (issue #863) |

The migration from inline `@app.*` endpoints to bounded routers is ongoing.
Remaining endpoint domains in `server.py` are tracked for extraction under issue #4.

Backend tests must resolve `backend/` imports consistently from a clean checkout.
The project pytest configuration declares `backend` on `pythonpath`, and
`tests/conftest.py` also inserts the resolved backend directory before importing
the FastAPI app and router dependencies.

**Auth test fixtures (issue #343):** `mock_auth` is opt-in (not `autouse`).
Tests that need to bypass authentication must declare `mock_auth` explicitly.
`make_principal(id, type, roles)` creates a minimal `Principal`; the helpers
`admin_principal`, `operator_principal`, and `viewer_principal` cover the
three main roles. `make_authed_client(principal)` returns a `TestClient` with
the given principal pre-wired.

**Uvicorn runtime tuning (env-var driven, issue #393):**

When `backend/server.py` is invoked as `__main__`, the uvicorn instance is
configured from environment variables so operators can adjust ASGI
behaviour without code changes:

| Variable             | Default | Purpose                                                                                                                                                                            |
| -------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WORKERS`            | `1`     | Worker process count. Stays at `1` until leader-election (#367) lands; setting it higher logs a runtime warning because background tasks would otherwise duplicate across workers. |
| `LIMIT_CONCURRENCY`  | `200`   | Max concurrent in-flight requests before uvicorn returns 503.                                                                                                                      |
| `TIMEOUT_KEEP_ALIVE` | `5`     | Seconds an idle keep-alive HTTP connection is held before closure.                                                                                                                 |

Invalid values fall back to the default and emit a log warning.

**Bounded in-process buffers:** the CPU sample buffer `_cpu_history` is a
`collections.deque` capped at `_CPU_HISTORY_MAXLEN` (1000 samples). The
fixed cap guarantees flat memory regardless of process uptime (#393).

### 2.2 Frontend

**Type:** Single-Page Application (SPA)
**Entry point:** `frontend/src/main.tsx` (built via Vite)
**Build step:** Vite + React + TypeScript (`npm run build` -> `dist/`)
**Type checking:** Deferred to Phase 2 (`npm run typecheck`, not blocking build)
**JavaScript framework:** React (imported as ES modules)
**createElement API:** `h()` alias (JSX migration in progress)
**Styling:** Extracted CSS in `frontend/src/index.css` (was inline)

Application logic is contained in `frontend/src/legacy/App.tsx` (migrated from
the previous single-file `frontend/index.html`). The Vite build outputs to
`dist/` which the FastAPI backend serves. A `package.json` with build tooling
is now present. Type checking is separated from the build pipeline to allow
incremental migration of legacy code; Phase 2+ will progressively fix type errors
as routes and components are extracted.
`frontend/perf-budget.json` records the issue #200 mobile performance budget.
The budget check enforces
the target mobile shell, tab chunk, Lighthouse, INP, and FCP values plus an
interim gzip ceiling for the built `dist/index.html` and its bundled JavaScript/CSS.
Budget increases require a PR that edits the budget file with justification.

Mobile layouts must remain usable at 375x812 and 412x915 viewport sizes. The
header tab strip is horizontally scrollable, nonessential header status badges
are hidden on mobile, Queue Health renders compact KPI/cards instead of forcing
wide tables, and Workflows filters use sessionStorage-backed state so tab
switching and app backgrounding do not reset the current session filters.
Reports, Assessments, and Feature Requests expose read-mostly mobile card and
reader layouts over their existing APIs so operators can inspect report files,
assessment score history, and feature request history without relying on wide
desktop tables.

The mobile foundation is documented in `docs/mobile-native-shell.md` and
`docs/mobile-design-system.md`. Reusable mobile
design contracts live in `frontend/src/design/*.ts` modules and are
guarded by pytest. The Fleet tab exposes a
mobile-only read surface for runner monitoring cards over the existing runner,
run, and machine telemetry payloads; desktop machine and runner tables remain
the canonical wide-screen surface.

**Offline mutation queue (issue #380):** When `navigator.onLine` is `false`, POST/DELETE/PATCH mutations that fail due to network error are persisted to IndexedDB via `frontend/src/lib/mutationQueue.ts` (backed by the `idb` library). Each queued entry carries a generated `Idempotency-Key` UUID so server-side duplicate execution is impossible on replay. On `window.online`, the queue is drained in FIFO order; entries older than 10 minutes require explicit user reconfirmation before replay. The `OfflineQueueIndicator` primitive in `frontend/src/primitives/OfflineQueueIndicator.tsx` renders an accessible `role="status"` badge showing offline state and pending-replay count.

Reusable UI primitives live in `frontend/src/primitives/`. Issue #422 introduces `Badge.tsx` (`tone` in `success | warning | danger | info | neutral`, `size` in `sm | md`) and `Pill.tsx` (with a `selected` boolean prop) so that the previously ad-hoc `.section-badge`, `.runner-status-badge`, `.conclusion-badge`, `.subtab-badge`, and `.fleet-status-pill` styles share a single token-driven implementation backed by `--badge-*-bg` / `--badge-*-fg` CSS variables in `frontend/src/design/tokens.ts`.

PushSettings (issue #192) is a mobile-friendly React component for per-topic Web Push subscription management. It is located at `frontend/src/pages/PushSettings.tsx` and uses `GET /api/push/vapid-public-key` to fetch the VAPID key before subscribing to selected push topics via `POST /api/push/subscribe`.
The Vite entrypoint in `frontend/src/main.tsx` includes a minimal tracer-bullet route shim for `/settings/push`: when the browser pathname resolves to that route, it renders `PushSettings` directly; all other paths continue to render the main dashboard app. This keeps the PushSettings work isolated while the Vite migration remains in progress.
Mobile accessibility guards are part of the frontend source contract. At
mobile viewport widths, primary interactive controls must use the shared
`--mobile-hit-target` token with a minimum `44px` target size. CSS animations
and transitions must respect `prefers-reduced-motion: reduce`, and inline
transition styles must opt out through `prefersReducedMotion()`. Static
frontend integrity tests enforce these guards alongside ARIA labels for mobile
summary sections and modal dialogs. The HTML viewport metadata must not disable
user scaling with `maximum-scale` or `user-scalable=no`.

The first issue #202 mobile test harness slice lives in `tests/frontend/mobile/`.
It defines the Playwright mobile viewport contract for `iphone-12` (390 x 844),
`pixel-5` (393 x 851), `epic-compact-375` (375 x 812), and
`epic-standard-412` (412 x 915), plus shared tap, swipe, and long-press helper
scaffolding. The current CI-safe guard is static pytest validation; browser
execution and screenshot baselines remain disabled until the harness proves
stable enough to add a non-flaky Playwright lane.

The first M04 touch primitive implementation slice lives in
`frontend/src/primitives/`. `TouchButton` wraps native buttons with the shared
mobile hit-target and press/focus affordance, while `SegmentedControl` provides
an accessible `radiogroup` for compact mobile filters. Gesture-heavy primitives
(`SwipeRow`, `PullToRefresh`, and `BottomSheet`) remain separate follow-up
work because they require pointer-event and focus-management tests.

**Shared helper components** defined near the top of the script block:

- `Collapse` â€” collapsible section with header and chevron.
- `SubTabs` â€” horizontal sub-tab strip rendered inside a tab panel. Props:
  `tabs` (array of `{ key, label, badge, disabled }`), `activeKey`, `onChange`,
  `storageKey` (optional localStorage persistence key), `rightBadge` (optional
  element flush-right of the strip). Active tab is persisted to localStorage
  when `storageKey` is provided.

#### Header Quick Dispatch

The main header contains a **Quick Dispatch** button (âš¡ Quick Dispatch â–¾),
flush-right next to the refresh control. Clicking it opens a popover form that
lets any operator dispatch an ad-hoc agent task to any org repository without
navigating to a specific tab. The popover provides:

- **Repository** dropdown â€” populated from `GET /api/repos`
- **Provider** dropdown â€” populated from `GET /api/agents/providers`
- **Model** text field â€” shown only for providers that support model selection
  (`claude_code_cli`, `codex_cli`); defaults to `claude-opus-4-7`
- **Branch ref** text field â€” defaults to `main`
- **Prompt** textarea â€” minimum 10 characters
- **Dispatch** button â€” POSTs to `POST /api/agents/quick-dispatch`; shows a
  loading state, surfaces errors inline, and auto-closes on success

Click-outside closes the popover. Rate-limit errors (HTTP 429) are surfaced
with a human-readable message.

`frontend/src/legacy/App.tsx` is the **sole canonical frontend source** during
Phase 1 of the Vite migration. `frontend/index.html` is now the minimal Vite
HTML shell. No other
frontend implementation exists in the repository. The previously present
`RunnerDashboard.jsx` was an unused JSX archive that violated DRY; it was
removed in issue #3 to enforce a single source of truth. A CI test
(`test_jsx_archive_removed`) prevents re-introduction of a parallel
implementation.

### 2.3 Deployment

The dashboard runs as a systemd service (`runner-dashboard.service`) on the
primary fleet machine. See Section 6 for deployment details.

`deploy/setup.sh` performs a `preflight()` check before any mutation (asserts
disk free >1G at the deploy dir, Python 3.11+, port 8321 availability, and
`~/.config/runner-dashboard/env` permissions of `600`), supports `--check-only`
to run preflight without side effects and `--dry-run` to preview intended
mutations, replaces `/etc/sudoers.d/runner-dashboard` atomically via
`visudo -c -f` against a temp file (validation failure leaves the existing
file untouched), and skips `systemctl restart runner-dashboard` when the
deployed `git_sha` in `deployment.json` matches the current checkout unless
`--force` is supplied.

#### 2.3.1 WSL boot stability (split-disk hosts)

Hosts that split runner storage across two physical disks run two systemd WSL2
distros in one shared utility VM. Two hardening steps keep them out of the
`WaitForBootProcess` crash loop (WSL aborts a distro boot — `reboot(RB_POWER_OFF)`
— if systemd does not reach its default target within ~10s):

- **`deploy/decouple-docker-boot.sh`** removes `docker.service` and
  `containerd.service` from the boot transaction (they stay installed and are
  not stopped), keeps `docker.socket` enabled for on-demand activation, and
  installs **`deploy/docker-delayed-start.timer`** (`OnBootSec=20s`) so Docker
  starts shortly after boot, off the critical path. On SSD-backed distros this
  drops boot-ready from ~15s to ~4–5s. Effective on next boot; idempotent.
- **`deploy/install-wsl-keepalive-task.ps1`** registers the resident keepalive
  with an **S4U principal** (`-LogonType S4U -RunLevel Highest`) so it runs
  whether or not the user is logged on, with an unbounded `ExecutionTimeLimit`
  and an `AtStartup` trigger. This keeps the host-side handle that holds the WSL
  VM resident across logoff, preventing the teardown that otherwise forces both
  distros to cold-boot together and race the 10s window.

#### 2.3.2 Disk-pressure controls (`runner-cleanup.sh`)

Docker (build cache + volumes + buildx builder containers) is the dominant disk
consumer on runner hosts. Two controls keep a distro from filling to 100% (which
crash-loops every runner with `No space left on device`):

- **Aggressive docker prune under pressure.** When the root filesystem crosses
  `DISK_PRESSURE_PERCENT` (default 85%), `runner-cleanup.sh` sets
  `DOCKER_AGGRESSIVE=1`: it prunes **all** build cache (including `docker buildx`
  builder caches), **all** unused images, and **dangling volumes**, ignoring the
  routine `DOCKER_PRUNE_UNTIL` age window. Pruning only removes stopped
  containers, unused images, dangling volumes, and idle cache, so running jobs
  and in-use volumes are never affected. Below the threshold, the routine pass
  keeps the age window so recent cache is preserved for build speed.
- **Hourly disk guard.** `runner-cleanup.sh --disk-guard` is a lightweight,
  runner-safe pass that reclaims docker + journal + `fstrim` **only** — it never
  stops runner units, so `install-runner-maintenance.sh` schedules it on an
  **hourly** `runner-disk-guard.timer` (the full cleanup, which bounces idle
  runners to clear `_work`, stays daily). This catches docker bloat long before
  the disk fills between daily cleanups.

---

## 3. Feature List â€” Dashboard Tabs

### 3.1 Fleet Tab

Real-time view of all self-hosted runners. Displays runner name, status (idle,
active, offline), current job, labels, and systemd service state. Provides
start/stop controls per runner and bulk fleet controls.

#### 3.1.1 Multi-Pool Fleet Status

`GET /api/fleet/status` aggregates multiple co-located runner-pool backends
into a single response. On a ControlTower host that runs two pool backends:

- Port **8321** — `ControlTower-NVMe` (primary, hub role)
- Port **8322** — `ControlTower-HDD` (secondary pool)

The primary dashboard instance (8321) queries the secondary (`?exclude_pools=true`)
and merges both pool entries under their pool names. If the secondary is
unreachable, its entry is set to `{"status": "offline", ...}` so the primary
view remains available. The `exclude_pools=true` query parameter prevents
recursive peer queries.

In the fleet deployment, dashboard-to-dashboard routing must prefer Tailscale
MagicDNS hostnames over ephemeral WSL-local or stale tailnet IP addresses. The
ControlTower pool URLs are canonicalized as:

- `http://controltower.tail2bbcc7.ts.net:8321` for `ControlTower-NVMe`
- `http://controltower.tail2bbcc7.ts.net:8322` for `ControlTower-SSD`

The per-node system proxy budget is 30 s for remote node telemetry. This keeps
loaded Windows hosts, especially DeskComputer during full runner load, from
being marked unhealthy while enumerating runner processes and host metrics.

Response shape (keyed by pool name, plus any FLEET_NODES peers):

```json
{
  "ControlTower-NVMe": { "status": "online", "hostname": "...", ... },
  "ControlTower-SSD":  { "status": "online",  "hostname": "...", ... },
  "OGLaptop":          { "status": "online",  "hostname": "...", ... }
}
```

The Fleet tab `Mobile.tsx` renders entries whose names start with `"ControlTower"`
in a dedicated **ControlTower Pools** section with a side-by-side card layout;
all other fleet nodes render as standard runner cards below that section.

#### 3.1.2 Persistent hardware-facts cache (cold-start budget)

`GET /api/fleet/status` and `GET /api/system` build their disk/storage-pool
section via `system_utils.get_storage_pools()`, which on WSL hosts probes the
Windows host for **static hardware facts** — a drive's `MediaType`/`BusType`
(`Get-PhysicalDisk`) and the distro's VHDX `BasePath` (Lxss registry). These
PowerShell probes can take ~10 s and ~1.3 s respectively on a cold cache. Since
the in-memory cache is empty after every `systemctl restart`, the first request
previously paid the full ~13 s and, together with the live Windows
host-resource snapshot, exceeded the 15 s `PROXY_TO_HUB_S` budget — surfacing as
HTTP 504 "Hub timeout".

Because these facts are static, they are persisted to
`~/.config/runner-dashboard/hardware_facts.json` (override the directory with
`RUNNER_DASHBOARD_STATE_DIR`). After the first warm-up the cold path is served
from disk in microseconds, so restarts no longer pay the probe tax. Each entry
carries a `_fetched_at` timestamp and is re-probed once per `_HW_FACTS_TTL_S`
(24 h) in case host topology changes; even a `Unknown/Unknown` failure result is
persisted so hosts where `Get-PhysicalDisk` is slow-but-empty stay fast. Every
live PowerShell probe is additionally bounded by a `_HW_PROBE_TIMEOUT_S` (4 s)
wall-clock deadline that clamps each candidate's subprocess timeout and stops
iterating candidates once exhausted, so a slow probe degrades to `Unknown`
instead of blocking the endpoint. The returned metric shape is unchanged.

Two further per-request subprocess costs in the same metrics path are bounded by
short-TTL caches so steady-state polling never re-pays them:

- **Live Windows host CPU/RAM** (`_windows_host_resource_snapshot`) forks a ~2 s
  PowerShell `Get-CimInstance` on every call. A `_HOST_SNAPSHOT_TTL_S` window
  (10 s, override `RUNNER_DASHBOARD_HOST_SNAPSHOT_TTL_S`) lets concurrent and
  successive callers share one fork; on a refresh failure the last good value is
  served rather than dropping to the WSL fallback.
- **Runner-capacity snapshot** (`get_runner_capacity_snapshot`) forks the
  runner-scheduler binary (`--dry-run --json`) plus two `systemctl is-active`
  calls (~2-3 s on a busy host). It is cached for `CacheTtl.RUNNER_CAPACITY_S`
  (15 s); the underlying schedule/timer state changes on the order of minutes,
  so the panel stays effectively live. Both the metrics endpoints and the
  orchestration schedule routes share the cache.

### 3.2 History Tab

Paginated workflow run history across all org repositories. Filterable by repo,
status, branch, and actor. Supports rerun and cancel actions on individual runs.

### 3.3 Queue Tab

Live view of queued and in-progress workflow jobs. Shows waiting time, assigned
runner, and blocking conditions. Supports bulk cancellation. Includes a
diagnostic endpoint to explain queue stalls. On mobile, the tab presents a
queued/running/stale KPI strip and compact queued-run cards; destructive cancel
actions require an explicit confirmation state that shows the number of runs
affected before the existing cancel endpoint is invoked.

### 3.4 Machines Tab

Multi-node fleet hardware inventory sourced from `machine_registry.yml`.
Displays per-node system metrics (CPU, RAM, disk, GPU VRAM) fetched via the
fleet nodes API. Supports drilling into individual node system status.

### 3.5 Organization Tab

Org-level runner and repository summary. Shows runner group assignments,
available label sets, and aggregate health across all repos.

### 3.5.1 `/api/linear/*` â€” optional Linear integration

When Linear is configured, the dashboard exposes:

- `GET /api/linear/workspaces` â€” configured workspaces with auth status
- `GET /api/linear/teams` â€” teams for one workspace or all configured workspaces
- `GET /api/linear/issues` â€” Linear-only issue inventory in canonical dashboard shape

If Linear is not configured, Linear-backed issue reads return HTTP 503 with the
standard not-configured detail. `GET /api/issues` accepts
`source={github|linear|unified}`; `github` remains the backward-compatible
default.

Issue #242 also adds a write-only inbound webhook surface for Linear. The
dashboard exposes `POST /api/linear/webhook` for Funnel-delivered webhook
events and `GET /api/linear/webhook/health` for operator health checks. The
receiver validates `Linear-Signature` when a secret is configured, bypasses
browser CSRF checks for this external-service route only, rejects stale
payloads older than 300 seconds, and deduplicates repeated `webhookId` values
to provide replay protection.

Issue #243 completes the Linear integration by wiring the webhook receiver to
the agent-agnostic dispatch path and adding a lightweight Credentials-tab
setup panel in `frontend/src/pages/LinearSetup.tsx`. The setup panel displays
the webhook URL, workspace auth/trigger metadata, and the operator-facing
instructions for configuring the inbound Linear webhook.

### 3.6 Tests Tab

Unified testing hub with two sections:

1. **CI Tests** â€” table of the latest `ci-standard` workflow run for each of
   the 17 fleet repos, showing conclusion badge, branch, run number, and
   timestamp. Failed or cancelled runs show a **Re-run Failed** button that
   calls GitHub's `rerun-failed-jobs` API.
2. **Integration Tests** â€” dispatches and monitors heavy integration test runs
   (MuJoCo, Drake, Pinocchio physics stacks). Lists repos eligible for heavy
   testing, dispatches parameterized workflows, and optionally triggers
   Docker-based test environments.

The tab uses shared `Badge`, `EmptyState`, and `TouchButton` primitives for
status labels, loading state, rerun actions, and heavy-test dispatch controls.
Token-backed `tests-tab__*` classes own table wrappers, links, headings, and
dispatch status metadata.

### 3.7 Stats Tab

Aggregate workflow statistics: success rates, average duration, failure
frequency, and per-repo breakdowns sourced from the `/api/stats` endpoint.

### 3.8 Reports Tab

Displays dated fleet report files (Markdown). Provides date selection and
renders the report with parsed metrics summary cards.
On mobile, report files render as tappable cards with date and size metadata,
the selected report uses a constrained reader with mobile typography, and an
Open raw link exposes the underlying report API response as a fallback.

### 3.9 Scheduled Workflows Tab

Inventory of all cron-scheduled workflows across the org. Shows next/previous
run times, schedule expressions, and allows manual dispatch of any scheduled
workflow. Status and latest-run conclusions render through the shared `Badge`
primitive, first-load placeholders use `SkeletonCard`, and empty/no-filter-match
states use the shared `EmptyState` surface so the tab follows the dashboard
design-token system.

### 3.10 Runner Plan Tab

Fleet autoscaler configuration. Displays current runner count, scaling policy,
schedule-based on/off windows, and allows adjusting the target runner count.
The Runner Schedule capacity editor uses shared `Badge`, `TouchButton`, and
`EmptyState` primitives plus token-backed `runner-schedule__*` classes for
scheduler status, save/apply actions, table inputs, and empty/error states.

### 3.11 Local Apps Tab

Health status of local registered applications (processes, services defined in
`local_apps.json`). Shows up/down state, PID, and restart commands.

### 3.12 Remediation Tab

AI agent dispatch control panel organised into three sub-tabs:

- **Automations** (default) â€” configures and dispatches remediation plans to
  Jules, GAAI, Claude, or Codex agents. Shows dispatch history and plan
  preview. Supports per-repo agent routing, loop-guard configuration, and
  provider fallback chain escalation.
- **PRs** â€” multi-select table of open pull requests fetched from
  `GET /api/prs?limit=2000`. Supports filtering by repo, author, and draft
  status. Bulk dispatch sends selected PRs to a chosen provider via
  `POST /api/prs/dispatch` with a confirmation modal.
- **Issues** â€” taxonomy-aware GitHub Issues browser and bulk dispatcher
  (`GET /api/issues?limit=2000`). Filter bar with repo, complexity,
  judgement, and "pickable only" controls persisted to `localStorage`.
  Multi-select table with type/complexity/effort/judgement pills. Non-pickable
  rows are dimmed; `design`/`contested` judgement pills rendered red with
  warning. Dispatches via `POST /api/issues/dispatch` with optional force flag.

The active sub-tab is persisted to `localStorage` under the key
`remediation-subtab`.

On mobile-width viewports, the Remediation sub-tabs render as a segmented
control. Tapping a failed run in Automations opens a bottom-sheet action surface
with the recommended-provider dispatch action, an optional provider picker, a
safety-plan preview action, and a desktop/run link. Mobile dispatch continues to
call the existing `/api/agent-remediation/dispatch` path; it does not introduce a
new dispatch envelope or bypass backend authorization and remediation invariants.
After dispatch submission, the Remediation tab shows an in-flight status tile
above the sub-tabs so the status remains visible while switching between
Automations, PRs, and Issues.

### 3.13 Workflows Tab

Browse and manually dispatch any workflow in any org repository. Supports
input parameter forms generated from workflow `workflow_dispatch` definitions.
Workflow search, repository, and trigger filters are persisted to
sessionStorage for the current browser session.

### 3.14 Credentials Tab

Inventory of GitHub Actions secrets and variables across the org and per-repo.
Read-only view of credential names (not values) for audit purposes.
On mobile-width viewports, the tab renders locked by default and only loads
credential metadata after a fresh WebAuthn assertion succeeds. Mobile
credential mutations require an explicit second confirmation in a bottom-sheet
dialog. `/api/credentials` requests are denylisted from frontend cache paths
and sent with `cache: "no-store"`; credential values are never rendered.

### 3.15 Assessments Tab

Dispatch and track code quality assessment workflows (Jules Assessment
Generator). Shows per-repo assessment scores from the `/api/assessments/scores`
endpoint.
On mobile, assessment score history renders as per-repo cards showing score,
provider, date, and summary while preserving the existing dispatch controls and
read endpoint.

### 3.16 Feature Requests Tab

Browse and submit feature request issues via templates. Allows dispatching
feature implementation workflows directly from the dashboard.
On mobile, dispatched feature request history renders as compact read-mostly
cards showing repository, status, vote-count metadata when present, provider,
date, and prompt excerpt over the existing `/api/feature-requests` response.

### 3.17 Maxwell Tab

Control interface for the Maxwell daemon (fleet orchestration AI). Shows
daemon status, configuration, start/stop/configure controls, and a mobile
operator chat surface with preserved history, quick actions, streamed replies,
and a daemon-unreachable retry state.

### 3.18 Fleet Orchestration Tab

Cross-node deployment orchestration. Shows orchestration run history,
dispatches multi-repo deployment plans, and monitors rolling deploy status.

### 3.19 Help Tab

In-app help chat powered by the `/api/help/chat` endpoint. Provides contextual
assistance about dashboard features and fleet operations.

The shell Help/About surface (#822) also hosts an "Ask the codebase" tab (#838):
a codebase Q&A assistant that reuses the streaming chat UI, lets the operator pick
a repository and supply its local `repo_root`, and forwards both through
`POST /api/maxwell/chat` so Maxwell-Daemon answers from the repository's source.
It degrades gracefully when the daemon is unreachable or lacks codebase support.

---

## 4. API Endpoint Catalogue

All endpoints are served under `http://localhost:8321/api/`.

### System and Health

| Method | Path            | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------ | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/system`   | Host system metrics (CPU, RAM, disk, GPU)                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| GET    | `/api/events`   | Fleet event-log feed (issue #863). Returns `{events:[{ts,severity,kind,title,detail,node?}], count, capacity}`, newest-first. `severity` ∈ info/warning/critical; `kind` ∈ runner_offline/runner_online/low_disk/saturation/watchdog. `limit` query param (1–500). Ring-buffered in `backend/fleet_events.py`; populated by the fleet-status poller, which classifies runner online/offline transitions (incl. offline-due-to-disk via per-node disk free GB / used %) and low-disk warnings. |
| GET    | `/api/health`   | Dashboard health probe — returns `status` plus GitHub connectivity/runner fields such as `github_api`, `github_check_seconds`, and `runners_registered` when available.                                                                                                                                                                                                                                                                                                                       |
| GET    | `/api/watchdog` | Watchdog status and last heartbeat                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| GET    | `/readyz`       | Readiness probe — runs dependency checks (GH_TOKEN, gh CLI, SQLite stores); returns 200 or 503 with `{status, checks}`                                                                                                                                                                                                                                                                                                                                                                        |
| GET    | `/livez`        | Liveness probe — returns `{“status”:”ok”}` with no I/O; always 200 if process is up                                                                                                                                                                                                                                                                                                                                                                                                           |
| GET    | `/metrics`      | Prometheus text exposition — HTTP request counts/latency, GH API calls, active leases, cache sizes, uptime (issue #330). No auth gate; scrape from `localhost` only.                                                                                                                                                                                                                                                                                                                          |

**Prometheus metrics (`/metrics`):**
Implemented in `backend/instrumentation.py` using the `prometheus_client` library.
Metrics exported:

- `dashboard_http_requests_total{method,path,status}` — counter
- `dashboard_http_request_duration_seconds{method,path}` — histogram
- `dashboard_gh_api_calls_total{result}` — counter (result: success/4xx/5xx/rate_limited)
- `dashboard_gh_api_duration_seconds` — histogram
- `dashboard_dispatch_envelopes_total{action,result}` — counter
- `dashboard_subprocess_spawns_total{cmd}` — counter
- `dashboard_subprocess_duration_seconds{cmd}` — histogram
- `dashboard_replay_dedup_hits_total` — counter
- `dashboard_active_leases{principal}` — gauge
- `dashboard_cache_size{cache_name}` — gauge
- `dashboard_runner_capacity{state}` — gauge
- `dashboard_gh_api_rate_limit_remaining` — gauge
- `dashboard_uptime_seconds` — gauge
- `dashboard_active_websocket_connections` — gauge

**Request ID correlation (`X-Request-ID`):**
Every HTTP response carries an `X-Request-ID` header (issue #331). The value
is the inbound `X-Request-ID` request header if present (echo-back), or a
freshly generated 12-hex-char ID. The request ID flows into every log record
via `contextvars`. Set `LOG_FORMAT=json` for newline-delimited JSON logs with
keys: `ts`, `level`, `module`, `msg`, `request_id`, `principal_id`, `path`.

**Session secret persistence (`SESSION_SECRET_SOURCE`):**
When the `SESSION_SECRET` environment variable is not set, the server resolves
the secret using the following priority order and reports the resolution mode
in `GET /readyz` as `session_secret_source`:

1. `”env”` — `SESSION_SECRET` env var was explicitly configured (recommended
   for production).
2. `”persisted”` — secret was read from
   `~/.config/runner-dashboard/session_secret` (written on first startup).
3. `”generated”` — no env var and no persisted file existed; a fresh secret
   was generated via `secrets.token_hex(32)` and written atomically (mode
   `0o600`) to `~/.config/runner-dashboard/session_secret`.

A `WARNING` is logged at startup whenever the env var is absent so operators
can detect the mode without querying the endpoint. The persisted file
directory can be overridden via the `RUNNER_DASHBOARD_SESSION_SECRET_DIR`
env var.

### Deployment and Drift

| Method | Path                               | Description                                                |
| ------ | ---------------------------------- | ---------------------------------------------------------- |
| GET    | `/api/deployment`                  | Current deployment metadata                                |
| GET    | `/api/deployment/expected-version` | Expected version from repo                                 |
| GET    | `/api/deployment/drift`            | Version drift between deployed and expected                |
| GET    | `/api/deployment/git-drift`        | Git-commit drift: HEAD vs origin/main with is_drifted flag |
| GET    | `/api/deployment/state`            | Full deployment state object                               |
| POST   | `/api/deployment/update-signal`    | Signal the update mechanism                                |

### Runners

| Method | Path                             | Description                                                                                                                 |
| ------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/runners`                   | All org runners with systemd service state; GitHub rate limits return HTTP 429 with `Retry-After` and `retry_after_seconds` |
| GET    | `/api/runners/matlab`            | MATLAB-capable runner subset                                                                                                |
| POST   | `/api/runners/{runner_id}/stop`  | Stop a runner's systemd service                                                                                             |
| POST   | `/api/runners/{runner_id}/start` | Start a runner's systemd service                                                                                            |

### Workflow Runs

| Method | Path                               | Description                       |
| ------ | ---------------------------------- | --------------------------------- |
| GET    | `/api/runs`                        | Recent workflow runs (all repos)  |
| GET    | `/api/runs/enriched`               | Runs with per-job enrichment data |
| GET    | `/api/runs/{repo}`                 | Runs for a specific repository    |
| POST   | `/api/runs/{repo}/cancel/{run_id}` | Cancel a workflow run             |
| POST   | `/api/runs/{repo}/rerun/{run_id}`  | Re-run a workflow run             |

### Queue

| Method | Path                         | Description                                                                                                                                                                                     |
| ------ | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/queue`                 | Current job queue (queued + in_progress). Includes `queued_jobs_count` — true job-level queue depth, counting `queued` jobs inside in_progress runs too (run-level `queued_count` misses them). |
| GET    | `/api/queue/status`          | Queue data with per-run `timing` breakdown (queue_wait_seconds, exec_seconds)                                                                                                                   |
| POST   | `/api/queue/cancel-workflow` | Cancel a queued workflow                                                                                                                                                                        |
| GET    | `/api/queue/diagnose`        | Diagnose queue stalls with runner capacity, queued job target counts, label breakdowns, unroutable label findings, sampled jobs, and bounded partial-failure errors                             |

### Push Notifications

| Method | Path                                    | Description                                                          |
| ------ | --------------------------------------- | -------------------------------------------------------------------- |
| POST   | `/api/push/subscribe`                   | Store or update the caller's Web Push subscription and topic list    |
| DELETE | `/api/push/subscribe/{subscription_id}` | Remove the caller's subscription; admins may remove any subscription |
| POST   | `/api/push/test`                        | Admin-only test send to the caller's matching subscriptions          |
| GET    | `/api/push/vapid-public-key`            | VAPID public key for Web Push subscription setup                     |

### Fleet

| Method | Path                                  | Description                                   |
| ------ | ------------------------------------- | --------------------------------------------- |
| GET    | `/api/fleet/status`                   | Aggregate fleet status summary                |
| POST   | `/api/fleet/control/{action}`         | Bulk fleet action (start-all, stop-all, etc.) |
| GET    | `/api/fleet/schedule`                 | Runner schedule configuration                 |
| POST   | `/api/fleet/schedule`                 | Update runner schedule                        |
| GET    | `/api/fleet/capacity`                 | Fleet capacity and utilization                |
| GET    | `/api/fleet/nodes`                    | All registered fleet nodes                    |
| GET    | `/api/fleet/hardware`                 | Per-node hardware specifications              |
| GET    | `/api/fleet/nodes/{node_name}/system` | System metrics for a specific node            |
| GET    | `/api/fleet/dispatch/actions`         | Available fleet dispatch actions              |
| POST   | `/api/fleet/dispatch/validate`        | Validate a dispatch payload                   |
| POST   | `/api/fleet/dispatch/submit`          | Submit a fleet dispatch job                   |
| GET    | `/api/fleet/orchestration`            | Orchestration run history                     |
| POST   | `/api/fleet/orchestration/dispatch`   | Dispatch an orchestration plan                |
| POST   | `/api/fleet/orchestration/deploy`     | Execute a multi-node deployment               |

### Workflows

| Method | Path                       | Description                           |
| ------ | -------------------------- | ------------------------------------- |
| GET    | `/api/workflows/list`      | All dispatchable workflows in the org |
| POST   | `/api/workflows/dispatch`  | Manually dispatch a workflow          |
| GET    | `/api/scheduled-workflows` | Cron-scheduled workflow inventory     |

### Repositories

| Method | Path         | Description                        |
| ------ | ------------ | ---------------------------------- |
| GET    | `/api/repos` | All org repositories with metadata |

### Reports

| Method | Path                        | Description                          |
| ------ | --------------------------- | ------------------------------------ |
| GET    | `/api/reports`              | List of available dated report files |
| GET    | `/api/reports/{date}`       | Report content for a specific date   |
| GET    | `/api/reports/{date}/chart` | Chart data from a dated report       |

### Tests

| Method | Path                        | Description                                                      |
| ------ | --------------------------- | ---------------------------------------------------------------- |
| GET    | `/api/tests/ci-results`     | Latest `ci-standard` run per fleet repo (17 repos, cached 120 s) |
| POST   | `/api/tests/rerun`          | Re-run failed jobs on a given workflow run (`{repo, run_id}`)    |
| GET    | `/api/heavy-tests/repos`    | Repos eligible for heavy integration testing                     |
| POST   | `/api/heavy-tests/dispatch` | Dispatch a heavy test workflow via GitHub Actions                |
| POST   | `/api/heavy-tests/docker`   | Dispatch a Docker-based heavy test run                           |

### Stats and Usage

| Method | Path         | Description                   |
| ------ | ------------ | ----------------------------- |
| GET    | `/api/stats` | Aggregate workflow statistics |
| GET    | `/api/usage` | Runner usage time-series data |

### Agent Remediation

| Method | Path                                    | Description                                     |
| ------ | --------------------------------------- | ----------------------------------------------- |
| GET    | `/api/agent-remediation/config`         | Current remediation configuration               |
| PUT    | `/api/agent-remediation/config`         | Update remediation configuration                |
| GET    | `/api/agent-remediation/workflows`      | Eligible workflows for remediation              |
| POST   | `/api/agent-remediation/plan`           | Generate a remediation plan                     |
| POST   | `/api/agent-remediation/dispatch`       | Dispatch a remediation plan (GAAI/Claude/Codex) |
| POST   | `/api/agent-remediation/dispatch-jules` | Dispatch via Jules API                          |
| GET    | `/api/agent-remediation/history`        | Remediation dispatch history                    |

### Quick Dispatch

| Method | Path                         | Description                                                                           |
| ------ | ---------------------------- | ------------------------------------------------------------------------------------- |
| GET    | `/api/providers/registry`    | Shared source-of-truth provider registry (dashboard + Conductor contract, issue #810) |
| GET    | `/api/agents/providers`      | Available agent providers and their availability status (legacy/back-compat)          |
| POST   | `/api/agents/quick-dispatch` | Dispatch an ad-hoc agent task to any repository                                       |

#### `GET /api/providers/registry` (issue #810)

The single source-of-truth provider registry consumed by both the dashboard UI
and the Conductor orchestrator. It eliminates the previously-duplicated provider
lists (dashboard `PROVIDERS`, Conductor `ProviderMeta`, and the ad-hoc
`_PROVIDERS_WITH_MODEL_SELECTION` set) and bridges the underscore (dashboard)
vs hyphen (Conductor) id mismatch by carrying _both_ ids on every entry.

All static provider metadata derives from the one canonical table
`backend/agent_remediation/provider_registry.py::PROVIDER_REGISTRY`; the legacy
`agent_remediation.PROVIDERS` dict is generated from it. The Conductor enum
string values (`task_classes`, `capabilities`, `auth_kinds`) are vendored in
`backend/conductor_constants.py` (no cross-repo runtime import) and guarded
against drift by `tests/api/test_conductor_constants.py`.

The Ollama server whose models are listed defaults to `http://localhost:11434`
and is overridable via the `DASHBOARD_OLLAMA_URL` environment variable. This is
required when the dashboard runs in WSL but Ollama runs on the Windows host
(WSL2 `localhost` does not reach the host) — the deployment sets it to a
WSL-reachable address such as the host's Tailscale IP. The live fetch is
resilient: if the server is unreachable, `models` is `[]` and `login_status`
reflects the failure rather than erroring.

CLI providers `claude-cli` and `codex-cli` authenticate via their interactive
**subscription login** (`claude login` / `codex login`), not a model API key —
the fleet deliberately injects no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`. Each
provider's `setup_hint` reflects this so operators are never told to set a key
the system does not use.

Response shape (`schema_version` `1.0.0`):

```jsonc
{
  "schema_version": "1.0.0",
  "providers": [
    {
      "id": "claude-cli", // Conductor hyphen id
      "dashboard_id": "claude_code_cli", // dashboard underscore id
      "label": "Claude Code CLI",
      "execution_mode": "local_exec",
      "dispatch_mode": "github_actions",
      "auth_mode": "github_app", // none | github_app | api_key | local
      "resource": "runner", // runner | local
      "capabilities": ["code_edit", "..."],
      "cost_per_task": 0.05,
      "max_concurrency": 1,
      "models": ["claude-opus-4", "..."], // live for Ollama, curated for CLIs
      "models_endpoint": null, // Ollama: .../api/tags
      "login_status": "authenticated", // authenticated|unauthenticated|error|unknown
      "login_detail": "Probe reports ready.",
      "setup_hint": "...",
      "experimental": false,
      "editable": true,
      "remote": false
    }
  ],
  "auth_kinds": ["none", "github_app", "api_key", "local"],
  "task_classes": ["format", "..."],
  "capabilities": ["code_edit", "..."]
}
```

`models[]` for the Ollama provider is fetched live from
`http://localhost:11434/api/tags` via an injectable, resilient fetcher: a
connection failure degrades to `models: []` and a reachability-reflecting
`login_status` — the endpoint never returns `500`. CLI providers expose a small
curated model list; `models.length > 0` is what now marks model-selection
support. `login_status` is derived from the existing availability/credential
probes and is contractually one of the four literals above.

`POST /api/agents/quick-dispatch` now performs a cached pre-flight backpressure
gate before dispatching. If `GET /readyz` would fail or no online
`d-sorg-fleet` runner is available, the route returns HTTP `503` with
`{"error":"not_ready","reason":...,"retry_after_seconds":30}` and a
`Retry-After` header. Successful accepts return HTTP `202`. Operators may
override the gate with `force=true`, which is logged in the quick-dispatch
audit trail.

### PR and Issue Dispatch

| Method | Path                               | Description                                                     |
| ------ | ---------------------------------- | --------------------------------------------------------------- |
| GET    | `/api/prs`                         | List open pull requests across the org with claim/link metadata |
| GET    | `/api/prs/{owner}/{repo}/{number}` | Single PR detail with checks and file count                     |
| GET    | `/api/issues`                      | List open issues with taxonomy and pickability                  |
| POST   | `/api/prs/dispatch`                | Bulk-dispatch agent tasks to selected PRs                       |
| POST   | `/api/issues/dispatch`             | Bulk-dispatch agent tasks to selected issues                    |

### Credentials

| Method | Path                           | Description                                           |
| ------ | ------------------------------ | ----------------------------------------------------- |
| GET    | `/api/credentials`             | Org and repo secrets/variables inventory (names only) |
| POST   | `/api/credentials/set-key`     | Securely set an API key for a provider                |
| POST   | `/api/credentials/clear-key`   | Remove an API key for a provider                      |
| POST   | `/api/credentials/launch-auth` | Launch a provider's browser auth flow in a subprocess |

### Runner Audit

| Method | Path                                | Description                                                   |
| ------ | ----------------------------------- | ------------------------------------------------------------- |
| GET    | `/api/runner-routing-audit`         | Recent workflow runs on GitHub-hosted runners (billing alert) |
| POST   | `/api/runner-routing-audit/refresh` | Trigger an immediate audit refresh                            |

The Runner Audit UI renders its not-yet-run/all-clear states through the shared
`EmptyState` primitive, its hosted-runner labels through `Badge`, and its refresh
action through `TouchButton`. Scoped `runner-audit__*` classes carry the table,
warning, metadata, and link presentation through design tokens instead of
inline style objects.

### Maxwell

| Method | Path                    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------ | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/maxwell/status`   | Maxwell daemon status and configuration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| POST   | `/api/maxwell/control`  | Control Maxwell daemon (start/stop/configure)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| POST   | `/api/maxwell/dispatch` | Dispatch an agent task to Maxwell-Daemon. Proxies to MD's confirmation-gated, idempotent `POST /api/dispatch` (issue #953) — the endpoint that actually enforces `hmac.compare_digest` on `confirmation_token` and keys idempotency on `idempotency_key`. Sends the exact `DispatchRequest` contract body (`confirmation_token`, `prompt`, `repo`, `idempotency_key`); the caller must supply `confirmation_token` and `prompt`. A daemon-side confirmation rejection surfaces as 403 and an idempotency conflict as 409, rather than being masked as a success. Replaces the prior target `POST /api/v1/tasks`, which silently dropped both fields.                                                                                               |
| POST   | `/api/maxwell/chat`     | Proxy Maxwell chat messages to MD's request/response `POST /api/chat` (codebase-scoped requests route to `POST /api/chat/codebase`). Multi-turn context is carried in MD's `messages[]` (translated from the UI's history; issue #957) — MD now rejects the legacy `history`/`stream` fields. The proxy parses the JSON `ChatResponse` and emits its `content` as a `text/plain` body (the UI previously rendered the raw JSON). Accepts optional `repo`/`repo_root` fields (issue #838) that scope a codebase Q&A session; they are forwarded to Maxwell-Daemon, which jails its agentic codebase tools to that root. If the daemon lacks codebase support (Maxwell_Daemon#948) it returns 501, which the proxy degrades into a readable message. |

### Assessments

| Method | Path                        | Description                        |
| ------ | --------------------------- | ---------------------------------- |
| GET    | `/api/assessments/scores`   | Per-repo assessment quality scores |
| POST   | `/api/assessments/dispatch` | Dispatch an assessment workflow    |

### Assistant (Issues #88, #89)

#### Context-Aware Chat (Issue #88)

| Method | Path                  | Description                                        |
| ------ | --------------------- | -------------------------------------------------- |
| POST   | `/api/assistant/chat` | Query assistant about dashboard state with context |

**Request body:**

```json
{
  "prompt": "Why did this workflow fail?",
  "context": {
    "current_tab": "remediation",
    "selected_run_id": 12345,
    "selected_items": [],
    "dashboard_state": { "...": "..." }
  },
  "provider": "claude_code_cli"
}
```

**Response:**

```json
{
  "response": "Based on the logs, the failure was...",
  "provider": "claude_code_cli",
  "context_used": {...},
  "timestamp": "2026-04-25T11:30:00+00:00"
}
```

#### Action Proposals (Issue #89)

| Method | Path                            | Description                                                 |
| ------ | ------------------------------- | ----------------------------------------------------------- |
| POST   | `/api/assistant/propose-action` | Propose an action based on user request (awaiting approval) |
| POST   | `/api/assistant/execute-action` | Execute an approved action with full details                |

**Propose request:**

```json
{
  "user_request": "Restart runner-5",
  "context": {...},
  "provider": "claude_code_cli"
}
```

**Propose response:**

```json
{
  "action_id": "a1b2c3d4",
  "action_type": "restart_runner",
  "description": "Restart runner-5 (will be offline ~30s)",
  "parameters": { "runner_name": "runner-5", "timeout_seconds": 300 },
  "risk_level": "medium",
  "rationale": "User requested restart for debugging",
  "estimated_duration_seconds": 30
}
```

**Execute request:**

```json
{
  "action_id": "a1b2c3d4",
  "approved": true,
  "operator_notes": "ok, proceed"
}
```

**Execute response:**

```json
{
  "success": true,
  "action_id": "a1b2c3d4",
  "result": "Runner 'runner-5' restart initiated",
  "execution_time_ms": 245
}
```

### Feature Requests

| Method | Path                              | Description                                |
| ------ | --------------------------------- | ------------------------------------------ |
| GET    | `/api/feature-requests`           | Feature request issues list                |
| GET    | `/api/feature-requests/templates` | Available feature request templates        |
| POST   | `/api/feature-requests/templates` | Create a new feature request template      |
| POST   | `/api/feature-requests/dispatch`  | Dispatch a feature implementation workflow |

### Local Apps

| Method | Path              | Description                                    |
| ------ | ----------------- | ---------------------------------------------- |
| GET    | `/api/local-apps` | Health status of registered local applications |

### Help

| Method | Path             | Description                                  |
| ------ | ---------------- | -------------------------------------------- |
| POST   | `/api/help/chat` | In-app help chat (context-aware AI response) |

### Diagnostics

| Method | Path                               | Description                                                                                                                                                                                                                                                                                                                                                                        |
| ------ | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/diagnostics`                 | **Operator deploy-health endpoint.** Always 200. Reports machine_registry load status (including the error message if load failed), fleet_federation source + peer count, leader-lock status, key file mtimes, cache config. Used by `deploy/deploy-check.sh` and any external monitoring. Schema is a stable contract — adding fields OK, removing/renaming is a breaking change. |
| GET    | `/api/diagnostics/summary`         | Consolidated diagnostics: PID, memory, WSL status, git commit, drift                                                                                                                                                                                                                                                                                                               |
| GET    | `/api/diagnostics/vhdx`            | VHDX attachment status for all WSL distributions via `Get-DiskImage` (powershell.exe); includes sharing-violation detection. Returns `distributions[]` (name, path, attached), `storage_incident`, `generated_at`. Gracefully returns empty list when running on Linux CI. (issue #756)                                                                                            |
| GET    | `/api/diagnostics/pool-recovery`   | Structured recovery guidance for pool failure scenarios: `vhdx_locked` (ERROR_SHARING_VIOLATION), `disk_full`, `wsl_boot_failure`. Each scenario has `id`, `title`, `description`, `warning` (nullable), and `steps[]`. Warns against restarting WSL during active Optimize-VHD compaction. Returns `scenarios[]`, `runbook_url`, `generated_at`. (issue #756)                     |
| POST   | `/api/diagnostics/restart-service` | Restart runner-dashboard systemd service (localhost only)                                                                                                                                                                                                                                                                                                                          |

### Launchers

| Method | Path                         | Description                                                                                                                      |
| ------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/api/launchers/generate`    | Generate Windows PowerShell launcher scripts on the Desktop                                                                      |
| GET    | `/api/agent-launcher/status` | Read cline agent scheduler pidfile and per-agent state                                                                           |
| POST   | `/api/agent-launcher/start`  | Start the cline agent scheduler; on Linux this detaches `agent_launcher.py` with `subprocess.Popen(..., start_new_session=True)` |
| POST   | `/api/agent-launcher/stop`   | Stop the cline agent scheduler via the launcher CLI                                                                              |

### Static Assets

| Method | Path                    | Description                                                                                                                                                                                                                                                                  |
| ------ | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/`                     | Serves `dist/index.html`                                                                                                                                                                                                                                                     |
| GET    | `/manifest.webmanifest` | PWA manifest                                                                                                                                                                                                                                                                 |
| GET    | `/icon.svg`             | App icon                                                                                                                                                                                                                                                                     |
| GET    | `/{full_path:path}`     | SPA deep-link fallback — serves `dist/index.html` (`text/html`) for any unmatched non-`/api/`, non-static GET so React Router routes (`/t/:tabId`, `/settings/push`) load on a cold request. Registered last; `/api/*` and known static prefixes are excluded and still 404. |

---

## 5. Configuration

### 5.1 Environment Variables

| Variable                          | Default                                                  | Description                                                                         |
| --------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `GITHUB_TOKEN`                    | (required)                                               | GitHub PAT or App token with org runner/workflow scopes                             |
| `GITHUB_ORG`                      | `D-sorganization`                                        | GitHub organization name                                                            |
| `DASHBOARD_PORT`                  | `8321`                                                   | HTTP port the server listens on                                                     |
| `DISPLAY_NAME`                    | `hostname`                                               | Display name shown in the UI header                                                 |
| `NUM_RUNNERS`                     | `12`                                                     | Target number of self-hosted runners                                                |
| `MAX_RUNNERS`                     | `NUM_RUNNERS`                                            | Hard cap on runner count                                                            |
| `RUNNER_DASHBOARD_REPO_ROOT`      | Parent of backend dir                                    | Repo root for relative path resolution                                              |
| `DASHBOARD_DISK_WARN_PERCENT`     | `85`                                                     | Disk usage % threshold for warning state                                            |
| `DASHBOARD_DISK_CRITICAL_PERCENT` | `92`                                                     | Disk usage % threshold for critical state                                           |
| `DASHBOARD_DISK_MIN_FREE_GB`      | `25`                                                     | Minimum free disk GB threshold                                                      |
| `RUNNER_ALIASES`                  | ``                                                       | Comma-separated runner name aliases                                                 |
| `RUNNER_SCHEDULE_CONFIG`          | `~/.config/runner-dashboard/runner-schedule.json`        | Path to schedule config                                                             |
| `RUNNER_SCHEDULER_BIN`            | `/usr/local/bin/runner-scheduler`                        | Runner scheduler binary path                                                        |
| `RUNNER_SCHEDULER_SERVICE`        | `runner-scheduler.service`                               | Scheduler systemd service name                                                      |
| `RUN_JOB_ENRICHMENT_LIMIT`        | `50`                                                     | Max runs to enrich with job data                                                    |
| `LOG_FILTER_PATHS`                | `/api/scheduled-workflows,/api/heavy-tests,/api/reports` | Comma-separated path prefixes sampled at 1/10 in request logs; errors always logged |

### 5.2 machine_registry.yml

Located at `backend/machine_registry.yml`. Defines the multi-node fleet.

**Path resolution and security:** `machine_registry.load_machine_registry()`
passes `backend/` (the module's own directory) and `~/.config/runner-dashboard/`
as explicit allowed roots to the security validator. This is intentional: the
deployed install (`~/actions-runners/dashboard/`) is not a git checkout, so
the validator's default git-repo-root inference returns None. Without the
explicit allow-list every load on a deployed host fails as "Config path
escapes allowed roots", silently disabling fleet federation. The `MACHINE_REGISTRY_PATH`
env var overrides the lookup; operators wishing to manage the registry as
host config can place it under `~/.config/runner-dashboard/`.

**Fleet federation auto-derivation:** when the `FLEET_NODES` env var is empty
and `AUTODERIVE_FLEET_NODES` is unset or truthy, the server iterates the
registry's machines and populates `FLEET_NODES` from each entry's
`dashboard_url` (preferred) or first `tailscale_nodes[].ip`. The local host
is excluded by hostname/alias match against `DISPLAY_NAME` (or `platform.node()`).
This removes the historical foot-gun of leaving `FLEET_NODES` unset in systemd
Environment= lines and the dashboard silently showing only the local machine.
The `/api/diagnostics` endpoint reports the effective source (`env`, `registry`,
or `empty`) so deploy validation can confirm it.

Registry entries may retain historical nodes for audit or rollback, but exactly
one live entry per physical host should be marked `preferred: true`. For the
ControlTower host, the preferred entry is the Windows/Tailscale node
`controltower.tail2bbcc7.ts.net`, and any obsolete WSL monitoring node remains
non-preferred. Dashboard URLs should be stable HTTP URLs reachable from the
tailnet and should not use loopback except for same-host-only helper endpoints.

The WSL mirrored-port helper is responsible for preserving those HTTP URLs
across WSL cold starts. Before dashboard startup it inspects `tailscale serve
status`, clears any conflicting binding with the matching protocol
(`--http`, `--https`, or `--tcp`), removes the Windows portproxy entry, and
restores the dashboard as an HTTP Tailscale Serve binding after startup.
Protocol-aware clearing is required because an HTTP Serve binding is not
removed by a TCP-only clear command.

Example structure:

```yaml
nodes:
  - name: primary-host
    hostname: primary.local
    role: primary
    runners: 12
    labels: [d-sorg-fleet, linux, x64]
  - name: secondary-host
    hostname: secondary.local
    role: secondary
    runners: 4
    labels: [d-sorg-fleet, linux, x64, gpu]
```

### 5.3 config/agent_remediation.json

Controls which agents are enabled for remediation dispatch and their routing
configuration (API keys, model selection, repo allow/deny lists).

### 5.4 config/runner-schedule.json

Defines on/off schedule windows for runner scaling. Used by the runner
scheduler daemon (`deploy/runner-scheduler.py`).

### 5.5 local_apps.json

Registry of local applications monitored by the Local Apps tab. Each entry
includes process name, expected PID file path, and restart command.

---

## 6. Deployment

> **Full operator guide:** [`docs/deployment-model.md`](docs/deployment-model.md)

### 6.1 Quick Start (Development)

```bash
git clone git@github.com:D-sorganization/runner-dashboard.git
cd runner-dashboard
./start-dashboard.sh
# Opens http://localhost:8321
```

### 6.2 Production Setup

Run the full setup script on the target machine:

```bash
bash deploy/setup.sh --runners 4 --machine-name ControlTower --role hub
```

Fleet node examples:

```bash
bash deploy/setup.sh --runners 8 --machine-name OG-Laptop
bash deploy/setup.sh --runners 8 --machine-name DeskComputer --runner-aliases desktop
bash deploy/setup.sh --runners 8 --machine-name ControlTower --role hub \
  --fleet-nodes "OG-Laptop:http://100.64.12.7:8321,DeskComputer:http://100.64.12.9:8321"
```

Node-specific runner counts in the setup script examples must reflect the
current fleet plan. OG-Laptop is documented as an eight-runner node, and hub
fleet-node examples use concrete Tailscale URL placeholders so operators can
replace addresses without changing the argument shape.

`setup.sh` performs:

1. Installs Python dependencies into a system venv.
2. Copies the systemd unit file (`runner-dashboard.service`) to
   `/etc/systemd/system/`.
3. Enables and starts the service.
4. Configures the `GITHUB_TOKEN` environment variable in the service unit.
5. (Optional) Installs the runner autoscaler service.

### 6.3 Updating a Deployed Instance

```bash
bash deploy/update-deployed.sh
```

This script:

1. Installs/updates Python backend dependencies via `pip_install` (from `deploy/lib.sh`).
2. Creates a timestamped backup of the current deploy directory (`.bak.YYYYMMDD_HHMMSS`)
   before any files are changed.
3. Copies updated backend, frontend, helpers, and `local_apps.json`.
4. Writes fresh `deployment.json` metadata.
5. Restarts `runner-dashboard.service` via systemd.
6. Verifies service health and GitHub API connectivity.

#### Dry-Run Mode

Preview all steps without executing any destructive operations:

```bash
bash deploy/update-deployed.sh --dry-run
# or
DRY_RUN=true bash deploy/update-deployed.sh
```

#### Artifact-Based Deployment

```bash
bash deploy/update-deployed.sh --artifact runner-dashboard-v4.0.1.tar.gz
```

### 6.4 Rollback

Every `update-deployed.sh` run creates an automatic backup before copying files.
To roll back:

```bash
# List available backups
bash deploy/rollback.sh --list

# Roll back to the most recent backup
bash deploy/rollback.sh

# Roll back to a specific backup
bash deploy/rollback.sh --to ~/actions-runners/dashboard.bak.20260422_093017

# Preview rollback without executing
bash deploy/rollback.sh --dry-run
```

### 6.5 systemd Service

The service unit (`deploy/runner-dashboard.service`) runs:

```
ExecStart=/path/to/venv/bin/uvicorn backend.server:app --host 0.0.0.0 --port 8321
```

Secrets are loaded from `~/.config/runner-dashboard/env` (GH_TOKEN, GITHUB_ORG,
NUM_RUNNERS, DISPLAY_NAME).

Log output: `sudo journalctl -u runner-dashboard -n 50 --no-pager`

Health check: `curl http://localhost:8321/api/health`

### 6.6 Runner Autoscaler Service

The optional autoscaler service (`deploy/runner-autoscaler.service`) runs
`backend/runner_autoscaler.py` as a daemon. It monitors queue depth and
adjusts the active runner count based on the policy defined in
`config/runner-schedule.json`.

**Busy detection contract.** Before stopping a runner the autoscaler MUST
treat the unit as busy when ANY of these signals fire (ordered by which
phase of the job lifecycle they cover):

1. **Pre-Worker pickup window.** The runner's `_work/_temp/_runner_file_commands/`
   directory has been modified within the last `RUNNER_PICKUP_DIR_MAX_AGE_SECONDS`
   (default 30s). The Listener writes to this directory the moment it accepts
   a job, before forking the `Runner.Worker`, so this signal closes the
   1-2s race window where MainPID has no Worker child but a job IS assigned.
   Older mtime → stale residue (NOT busy); cleanup will GC it.
2. **Worker running.** A fresh job-pickup lockfile exists at
   `$RUNNER_BUSY_LOCK_DIR/<runner-name>.lock`, written by the runner's
   `ACTIONS_RUNNER_HOOK_JOB_STARTED` hook (see
   `deploy/runner-hooks/job-started.sh`). Catches the inverse window where
   the Worker exists but psutil's child-walk transiently misses it.
   Lockfiles older than `RUNNER_BUSY_LOCK_MAX_AGE_SECONDS` (default 24h)
   are treated as stale and ignored.
3. **Process tree.** The unit's MainPID has a `Runner.Worker` child process.
   The most direct signal once the Worker has forked and stabilized.
4. **Conservative fallback.** MainPID is 0/unknown but `ActiveState=active`
   and `SubState=running` — treat as busy during transient restarts.

The shell cleanup script `deploy/runner-cleanup.sh` honours the same
contract and additionally garbage-collects stale lockfiles. The
`/run/user/$UID/`, `~/.cache/`, and `/tmp/` fallbacks for the autoscaler's
own self-lock are defined in `_acquire_lock()` (the hard-coded `/var/run/`
path is unwritable for non-root deploys; see #664 follow-up).

**I/O pressure contract.** The autoscaler samples Linux PSI metrics from
`/proc/pressure/io` when available. `full avg10` above
`AUTOSCALER_IO_PRESSURE_FULL_HIGH` is overload, even if CPU and RAM are below
their limits, because it means all runnable work is blocked on filesystem I/O.
Scale-up recovery is blocked until `full avg10` is below
`AUTOSCALER_IO_PRESSURE_FULL_LOW`. After any start/stop action,
`AUTOSCALER_ACTION_COOLDOWN_SECONDS` prevents another scale action until the
host has had time to stabilize.

### 6.7 Runner Service Unit Template

GitHub Actions runner units installed by the runner package use
`KillMode=process` by default, which orphans `Runner.Worker` children when
the listener is stopped. Existing units are migrated to `KillMode=mixed`
plus the job-pickup hooks above by running
`sudo deploy/migrate-runner-units.sh` once per host. The migration uses a
systemd drop-in (`/etc/systemd/system/<unit>.d/10-runner-dashboard-busy-lock.conf`)
so it survives upstream runner package upgrades.

### 6.7 Shared Deploy Library

`deploy/lib.sh` is sourced by all deploy scripts and provides:

- Terminal colours and `ok`/`info`/`warn`/`fail` log helpers
- Guard assertions: `require_dir`, `require_file`, `require_cmd`
- `pip_install <pkg...>` â€” Python 3.11-preferring pip with `--break-system-packages` when supported
- `sync_dir <src> <dest>` â€” rsync with rm/cp fallback
- `backup_dir <path>` â€” timestamped `cp -a` backup
- `dry_run "<description>"` â€” no-op gate when `DRY_RUN=true`

All new deploy scripts should source it with:

```bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
```

---

## 7. Changelog

### 2.5.26 - 2026-05-21

- ci: added a documented workflow concurrency policy allowlist for
  `cancel-in-progress: false` exceptions and intentional PR-singleton
  workflows, with shared lint/test enforcement and operator triage guidance.

### 2.5.25 - 2026-05-07

- fix(frontend): install a document-level wheel guard for focused numeric,
  range, and select controls in the legacy dashboard so scroll gestures no
  longer mutate editable values by accident.

### 2.5.24 - 2026-05-02

- ci: made Python and frontend validation path-aware for pull requests while
  preserving full backend/frontend suites on `main`, so workflow-only changes
  still run static workflow gates without being blocked by unrelated source
  test debt.

### 2.5.23 - 2026-05-02

- ci: reduced duplicate remediation work by making Control Tower the single
  automatic CI-remediation owner, converting PR AutoFix to reusable/manual
  execution, replacing tight PR-check polling with bounded REST Actions lookup,
  and adding explicit deferred/manual verification states.
- ci: reduced scheduled dashboard/reaper churn with credential preflights,
  single-flight concurrency, less frequent schedules, bounded lease reaper
  runtime, and consistent action SHA pinning.
- ci: kept the 500-line quality gate strict on `main` while limiting pull
  request enforcement to changed source files so historical line-count debt
  does not block unrelated workflow reliability fixes.

### 2.5.22 - 2026-05-01

- fix: `gh_api` exposes GitHub rate limits as `RateLimitedError` with
  `retry_after_seconds`, records a per-token/resource in-memory breaker, and
  `/api/runners` translates the condition to HTTP 429 with `Retry-After`.

### 2.5.16 - 2026-04-30

- ci: keep the standard test lane aligned with the checked-in `uv.lock`, Bandit
  allow-list policy, and mypy relaxed-override module-count guard.
- chore(deploy): keep Docker and setup static guards on the supported Python
  3.11 runtime and systemd hardening contract.
- security: upgrade Docker image packaging utilities with available CVE fixes
  during the container build while preserving the locked application install.

### 2.5.14 - 2026-04-30

- feat(scalability): drive uvicorn `workers`, `limit_concurrency`, and
  `timeout_keep_alive` from `WORKERS` / `LIMIT_CONCURRENCY` /
  `TIMEOUT_KEEP_ALIVE` env vars, with defaults `1` / `200` / `5`. `WORKERS`
  stays at 1 until leader-election (#367) lands; setting it higher emits a
  runtime warning. Documented under §2.1 Backend (#393).
- chore(reliability): cap `_cpu_history` to a `collections.deque` with
  `maxlen=1000` so the in-process CPU sample buffer cannot grow without
  bound (#393).
- chore(reliability): cap `queue_cleanup.find_stale_runs` fan-out to 8
  concurrent repo queries via `asyncio.Semaphore` (#393).

### 2.5.11 - 2026-04-29

- feat: add authenticated session tracking and remote logout endpoints for the
  mobile auth surface, including hashed session listing and bulk revocation.

### 2.5.10 - 2026-04-29

- feat: add VAPID public key endpoint (`/api/push/vapid-public-key`) and `PushSettings` frontend component with per-topic subscription toggles for Web Push notifications (issue #192).
- feat: route `/settings/push` from `frontend/src/main.tsx` to `PushSettings` so the Vite entrypoint exposes the `#173` tracer-bullet path during Phase 1 migration.
- feat: add the first M04 touch primitive implementation slice with
  `TouchButton` and `SegmentedControl` contracts.

### 2.5.8 - 2026-04-29

- test: add explicit epic acceptance viewport profiles for 375x812 and
  412x915 to the mobile test harness.

### 2.5.7 - 2026-04-29

- feat: add the mobile integration foundation for native-shell selection,
  static design tokens, and read-only Fleet runner monitoring cards without
  changing the built frontend runtime.

### 2.5.6 - 2026-04-29

- test: add the issue #202 mobile Playwright harness contract with checked-in
  viewport profiles, touch helper scaffolding, and static validation before
  enabling browser or visual-regression CI.

### 2.5.2 — 2026-04-28

- chore: migrate to `uv` for dependency management and add `uv.lock`.
- ci: refactor CI workflows to be `uv`-native, ensuring reproducible builds and faster bootstrap times (resolves #163).
- ci: updated `ci-spec-check` to monitor `uv.lock` for freshness.
- fix: include `itsdangerous` in the `uv` dependency set so Starlette session middleware imports during test collection.

### 2.0.0 â€” 2026-04-23

Initial standalone release. Extracted from the `D-sorganization/Repository_Management`
mono-repo as an independent repository.

- Full FastAPI backend with all API endpoints documented above.
- Vite-built React SPA frontend.
- Fleet deployment scripts and systemd service unit.
- Fleet-standard CI/CD workflows (ci-standard, ci-spec-check, agent workflows).
- Branch protection with required `quality-gate` and `Verify SPEC.md freshness`
  status checks.
- The `ci-health-check` bootstrap gate must allow enough time for a fresh
  runner to create a Python virtual environment, install `requirements.txt`,
  and collect tests before downstream quality, security, and test jobs run.
- Multi-agent coordination via lease protocol.

Prior versions tracked in the mono-repo `Repository_Management`. Application
version history in `VERSION` file (4.0.1 at time of extraction).

---

## 8. Testing

The project test suite lives in `tests/`. Run all tests with:

```
pytest tests/ -q
```

Pytest is configured with `pythonpath = ["backend"]` in `pyproject.toml`.
`tests/conftest.py` also inserts the resolved backend directory into
`sys.path` so local and CI runs import backend modules consistently from any
supported working directory.

Test coverage areas:

- **`tests/test_dispatch_contract.py`** â€” unit tests for `backend/dispatch_contract.py`:
  envelope round-trips, confirmation gating for privileged actions, allowlist enforcement.
- **`tests/test_remote_execution_contract.py`** â€” unit tests for `backend/remote_execution_contract.py`:
  private-host and private-URL detection, unknown-target rejection.
- **`tests/test_agent_remediation.py`** â€” unit tests for `backend/agent_remediation.py`:
  `FailureContext` construction, workflow-type classification for lint and test workflows,
  policy defaults.
- **`tests/test_frontend_integrity.py`** â€” static source checks for `frontend/src/legacy/App.tsx`:
  required tab function markers, absence of deprecated `HeavyTestsTab`, icon helper symbols.
- **`tests/test_frontend_perf_budget.py`** â€” validates `frontend/perf-budget.json`
  and enforces the interim gzip ceiling for the current built frontend artifact.
- **`tests/test_mobile_test_harness.py`** - validates the issue #202 mobile
  viewport profiles, smoke-page marker contract, touch helper exports, and the
  explicit visual-regression opt-in gate.
- **`tests/api/test_push.py`** - tests for `backend/push.py` VAPID public key endpoint response shape and principal import integrity.

### 8.1 Playwright E2E Smoke Tests (Issue #389)

`tests/e2e/smoke.spec.ts` — Playwright tests covering page load and basic navigation.

Run with: `npm run test:e2e`

Configuration is in `playwright.config.ts` at the repo root. Viewport profiles
are sourced from `tests/frontend/mobile/viewport_profiles.json` to keep mobile
smoke tests in sync with the Playwright suite. The CI workflow
`.github/workflows/frontend-tests.yml` runs Playwright Chromium smoke tests as
a blocking e2e job that gates merge on `main`.

Coverage:

- Root page loads with correct title; React `#root` element is non-empty; no top-level JS errors
- Fleet tab visible in navigation; renders content (runner cards, loading state, or empty state)
- Queue tab renders without crashing
- Maxwell tab degrades gracefully when daemon is offline (shows error/retry state, not blank)
- AgentDispatch page renders when accessible
- PushSettings page renders when accessible
- Navigation landmarks (nav, tablist) are present and visible when rendered
- Root path returns HTTP 2xx

`pytest>=8.0` and `pytest-asyncio>=0.23` are listed in `requirements.txt`.

---

## 9. Security

### 9.1 Markdown Rendering

All user-supplied content rendered as Markdown is passed through
`DOMPurify.sanitize()` before `dangerouslySetInnerHTML`. Marked.js is
configured with `{ mangle: false, headerIds: false, gfm: true }`.

### 9.2 Identity, Authorization, Attribution

The dashboard employs a strict Identity and Authorization model to secure access to the fleet.

**Identity Model:**
A **principal** is either a human or a bot/agent. Both have the same shape:

- `id`: Unique identifier (e.g., `human:dieter`, `bot:claude`).
- `type`: `human` or `bot`.
- `roles`: Assigned roles (`admin`, `operator`, `viewer`, `bot`), which expand into specific action scopes.
- `quotas`: Resource limits (runners, agent spend, app slots).

Principals are stored in `config/principals.yml`. The system fails closed: requests without a valid principal are rejected (HTTP 401).

**Loopback bypass (issue #315):** The loopback admin shortcut (granting automatic admin access to 127.0.0.1) is disabled by default. It is only active when `DASHBOARD_LOOPBACK_AUTH=1` is explicitly set in the environment. This must never be set in production deployments — it is intended solely for local single-user development where the dashboard is not reachable beyond 127.0.0.1.

**Authorization:**
All mutating `/api/*` endpoints require a principal.

- Humans authenticate via session cookies (from GitHub OAuth).
- Bots authenticate via `Authorization: Bearer <token>`.
- Human logins also register a durable dashboard session record in
  `~/.config/runner-dashboard/sessions.json` (overridable via
  `DASHBOARD_SESSIONS_PATH`) with `session_id`, `principal_id`, timestamps,
  user agent, IP address, and optional revocation time.
- Session records expire after `DASHBOARD_SESSION_TTL_SECONDS` (default 86400
  seconds), cap each principal at `DASHBOARD_MAX_SESSIONS_PER_PRINCIPAL`
  active sessions (default 10), and expose only hashed session identifiers to
  API callers.
- Auth routes now include `GET /api/auth/sessions` for listing active sessions,
  `DELETE /api/auth/sessions/{session_id_hash}` for per-session remote logout,
  and `POST /api/auth/logout/all` for bulk revocation with
  `exclude_current=true` by default.
  Scopes are enforced per-endpoint using the `require_scope(scope_name)` dependency.

**Mobile Biometric Unlock (WebAuthn):**
The WebAuthn route surface is additive to the existing session model. The
registration and assertion begin endpoints require an already authenticated
principal and issue short-lived, HMAC-signed server challenges under
`/api/auth/webauthn/*`. Credential metadata is stored per principal as
`(user_id, credential_id, public_key, sign_count)` and can be listed or revoked
by the owning principal. Completion endpoints intentionally fail closed until a
pinned WebAuthn verifier validates attestation/assertion payloads and sign-count
replay protection.

**Scope Presets:**

- `admin` â€” Full access to all endpoints.
- `operator` â€” Access to runners, workflows, and remediation dispatch.
- `viewer` â€” Read-only access (default for unprivileged tokens).
- `bot` â€” Scoped for agent tasks (remediation, workflows).

**Audit Logging & Attribution:**
Every mutating action is recorded in `DispatchAuditLogEntry` with dual-attribution:

- `principal` â€” The ID of the authenticated user/agent.
- `on_behalf_of` â€” Optional secondary attribution (e.g. when an admin impersonates a bot for debugging, the bot is the principal, and the admin is `on_behalf_of`).
- `correlation_id` â€” Propagated across fleet nodes for distributed tracing.

**Admin Impersonation Flow:**
An admin can act as another principal (like a bot) for debugging. By providing the `X-Impersonate-Principal: <bot_id>` header, the admin adopts the target principal's scopes. The audit log records the target as the `principal` and the admin as `on_behalf_of`.

**Onboarding a New Human:**

1. Add the human to `config/principals.yml` with `type: human`.
2. Assign appropriate `roles` (e.g., `operator`, `viewer`).
3. Set their `quotas`.

**Onboarding a New Bot:**

1. Add the bot to `config/principals.yml` with `type: bot`.
2. Assign the `bot` role.
3. As an admin, generate a service token for the bot: `POST /api/principals/<bot_id>/token`.
4. Provide the generated token to the bot agent for API access.

### 9.3 Request Body Size Enforcement (Issue #350)

`MaxBodySizeMiddleware` in `backend/middleware.py` rejects oversized requests
before routing:

- Default cap: **1 MB** (Content-Length > 1 048 576 bytes → HTTP 413).
- Webhook cap: **256 KB** for `/api/linear/webhook` (configured via
  `_LIMIT_OVERRIDES`).
- Per-route override: `@limit_body_size(bytes)` decorator sets
  `func.__max_body_size__`; the functional `max_body_size_check` middleware
  walks the `__wrapped__` chain to find it.
- Requests with no `Content-Length` header (streaming/chunked) are allowed
  through.
- Only mutating methods (POST, PUT, PATCH, DELETE) are checked.

### 9.4 HTTP Security Headers

The backend injects the following headers on all responses:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` â€” allows self, CDN scripts (jsdelivr, cdnjs, unpkg)

### 9.3 Destructive Action Confirmation

Critical fleet operations (runner stop, fleet restart) use a two-step
inline confirmation UI instead of `window.confirm()`.

### 9.4 Token Handling

`GH_TOKEN` and `ANTHROPIC_API_KEY` must be supplied as environment variables
only â€” never hardcoded in source files or configuration. The recommended setup
path is the `configure-env-vars.sh` script, which writes tokens to the systemd
override file so they are not visible in the process environment of child
processes and are not stored in shell history.

### 9.5 Network Exposure

The dashboard backend binds to `0.0.0.0:8321` by default so that multi-node
fleet monitoring works across the local network. Operators who do not need
cross-node access should bind to `127.0.0.1` instead (set the `HOST`
environment variable or modify the `systemd` unit file). No TLS is provided
by the dashboard itself; use a reverse proxy (nginx, Caddy) in front of the
service when HTTPS is required.

### 9.6 Operator Hardening Checklist

- Restrict network access to port 8321 via firewall rules (`ufw`, `iptables`,
  or cloud security groups); do not expose it publicly.
- Rotate `GH_TOKEN` and `ANTHROPIC_API_KEY` on a regular schedule (at minimum
  whenever a team member departs).
- Keep Python dependencies current: run `pip-audit` and `pip install -U -r
requirements.txt` during routine maintenance windows.
- Review agent dispatch logs in the Remediation tab regularly to detect
  unexpected or unauthorized agent invocations.
- Consider binding to `127.0.0.1` and using a reverse proxy with
  authentication if the dashboard is accessible to untrusted network segments.

### 9.7 Prompt Injection Sanitization

All user-controlled text inserted into LLM agent prompts (workflow failure
messages, log excerpts, issue bodies, PR descriptions) is passed through
`sanitize_for_prompt()` in `backend/agent_remediation.py` before inclusion.
The function:

- Truncates input to a configurable `max_length` (default 2000 chars) to
  limit token usage and reduce attack surface.
- Wraps the content in `[START_UNTRUSTED_CONTENT]` / `[END_UNTRUSTED_CONTENT]`
  delimiters so the model can distinguish trusted instructions from external
  data.

Every generated prompt also includes the constant
`PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION` as a preamble, instructing the model
not to follow any instructions found inside the delimiters.

### 9.8 Secret Scanning (Issue #396)

The repo enforces a defence-in-depth gate against accidentally committed
credentials:

- `gitleaks` and `detect-secrets` run as `pre-commit` hooks (configured in
  `.pre-commit-config.yaml`, both pinned by SHA).
- A dedicated `CI Secrets` workflow (`.github/workflows/ci-secrets.yml`)
  runs `gitleaks` on every pull request and push to `main`, plus a
  `detect-secrets` baseline-integrity check that fails when any new
  finding appears outside the audited `.secrets.baseline`.
- `tests/test_no_secrets_in_repo.py` runs in the standard pytest suite and
  greps every git-tracked file for well-known credential prefixes
  (GitHub PATs, AWS access keys, OpenAI / Anthropic / Slack tokens, PEM
  private-key blocks). Inline `# pragma: allowlist secret` suppresses a
  single line; `_ALLOWED_PATHS` skips known-safe files (the baseline, the
  gitleaks config, this test file).
- `.gitleaks.toml` extends the upstream default ruleset with allowlists
  for the well-known fake VAPID test key in `backend/push.py` (tracked
  for removal as a follow-up under #396) and standard documentation
  placeholders.
- Operational procedure (rotation, baseline refresh, leak response) lives
  in `docs/runbooks/secret-scanning.md`.

### 9.9 YAML Config Path Validation (Issue #355)

All YAML configuration loaders (`machine_registry.py`, `identity.py`,
`runner_lease.py`, `quota_enforcement.py`) validate paths before loading:

- `validate_config_path(path, allowed_roots)` in `backend/security.py`
  resolves the path and confirms it is within allowed roots
  (`~/.config/runner-dashboard` and `<repo>/config`).
- Symlinks are rejected if they point outside the allowed root.
- World-writable files (mode bits `o+w`) are rejected.
- `safe_yaml_load(path, allowed_roots)` combines path validation with
  `yaml.safe_load` into a single safe entry point.

### 9.10 Supply-Chain Security — Release Signing, Provenance, SBOM (Issue #392)

Every tagged release produced by `.github/workflows/release.yml` ships
a verifiable supply-chain artefact set:

- **Artifact tarball** — `dashboard-<VERSION>.tar.gz` excludes `.git`,
  `node_modules`, `venv`, `__pycache__`, and build artefacts.
- **SHA-256 checksum** — `dashboard-<VERSION>.tar.gz.sha256` for offline
  integrity verification.
- **Cosign keyless signature** — `dashboard-<VERSION>.sig` (+ `.pem` cert),
  signed with `sigstore/cosign-installer` using OIDC identity
  (`COSIGN_EXPERIMENTAL=1`; no long-lived key). Verify with:
  ```
  cosign verify-blob \
    --certificate dashboard-<VERSION>.pem \
    --signature  dashboard-<VERSION>.sig \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp "https://github.com/D-sorganization/runner-dashboard/.github/workflows/release.yml" \
    dashboard-<VERSION>.tar.gz
  ```
- **SLSA-3 build provenance** — generated by `actions/attest-build-provenance`
  and attached to the release as a GitHub attestation. Verify with:
  `gh attestation verify dashboard-<VERSION>.tar.gz --owner D-sorganization`
- **SPDX SBOM** — `sbom.spdx.json` produced by `anchore/sbom-action` (syft)
  and attached to the release.

All `uses:` references in the workflow are SHA-pinned (validated by
`tests/test_release_workflow_yaml.py` and
`tests/test_workflow_action_pinning.py`).

---

## 10. Prompt Notes and Agent Dispatch Configuration

### 10.1 User-Configurable Prompt Notes

The AI agent dispatch system supports user-defined preamble notes injected
before every outbound LLM prompt. These are stored in
`~/.config/runner-dashboard/prompt_notes.json` with the shape:

```json
{ "enabled": true, "notes": "Always prefer Python 3.11+ idioms." }
```

The `/api/feature-requests/templates` (GET) route returns the current notes
alongside prompt templates and engineering standards. The
`/api/feature-requests` (POST) route merges notes into the prompt before
dispatch when `enabled` is true and `notes` is non-empty.

### 10.2 Secure Environment Variable Setup

`deploy/configure-env-vars.sh` provides a guided interactive script for
setting `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, and other required environment
variables into the WSL systemd unit. It validates token format and writes
variables to the service override file rather than to shell rc files, reducing
the risk of secrets leaking through shell history.

### 10.3 Deployment Dependency Management

`deploy/setup.sh` and `deploy/update-deployed.sh` install Python dependencies
from `backend/requirements.txt` directly (via `pip install -r
backend/requirements.txt`) rather than a hardcoded list, ensuring the deployed
dependency set stays in sync with the source of truth automatically.

---

## 11. PR Inventory API

Implemented in `backend/pr_inventory.py`; thin route shells in `backend/server.py`.

### 11.1 `GET /api/prs`

Aggregates open pull-requests across organisation repositories.

**Query parameters:**

| Parameter        | Type                | Default       | Description                            |
| ---------------- | ------------------- | ------------- | -------------------------------------- |
| `repo`           | string (repeatable) | all org repos | Filter to specific `owner/repo` slugs  |
| `include_drafts` | bool                | `true`        | Include draft PRs                      |
| `author`         | string              | â€”           | Filter by author login                 |
| `label`          | string (repeatable) | â€”           | Match any of these labels              |
| `limit`          | int                 | 500           | Maximum items returned (hard cap 2000) |

**Response:**

```json
{
  "items": [
    {
      "repository": "D-sorganization/runner-dashboard",
      "number": 76,
      "title": "...",
      "url": "...",
      "author": "dieter",
      "draft": false,
      "age_hours": 12.3,
      "labels": ["bug", "ci"],
      "requested_reviewers": ["alice"],
      "head_ref": "fix/something",
      "mergeable_state": "clean",
      "agent_claim": null,
      "linked_issues": [24, 43]
    }
  ],
  "total": 1,
  "errors": []
}
```

- `agent_claim` â€” extracted from any `claim:*` label on the PR.
- `linked_issues` â€” issue numbers found via `closes/fixes/resolves #N` in the PR body.
- `errors` â€” per-repo error messages; a failing repo does not abort the whole request.
- Responses are cached 30 seconds in-process keyed by query parameters.

### 11.2 `GET /api/prs/{owner}/{repo}/{number}`

Returns single-PR detail with extra fields not present in the list endpoint:

| Field           | Description                                                      |
| --------------- | ---------------------------------------------------------------- |
| `body_excerpt`  | First 2 KB of the PR body                                        |
| `checks`        | List of `{name, conclusion, url}` from the commit check-runs API |
| `files_changed` | Number of changed files                                          |
| `additions`     | Lines added                                                      |
| `deletions`     | Lines deleted                                                    |

---

## 12. Issue Inventory API

Implemented in `backend/issue_inventory.py`; thin route shell in `backend/server.py`.

### 12.1 `GET /api/issues`

Aggregates open issues across organisation repositories with taxonomy-aware
filtering.

**Query parameters:**

| Parameter       | Type                | Default       | Description                                   |
| --------------- | ------------------- | ------------- | --------------------------------------------- |
| `repo`          | string (repeatable) | all org repos | Filter to specific `owner/repo` slugs         |
| `state`         | `open` \| `all`     | `open`        | Issue state                                   |
| `label`         | string (repeatable) | â€”           | Match any of these labels                     |
| `assignee`      | string              | â€”           | Filter by assignee login                      |
| `pickable_only` | bool                | `false`       | Only return issues available for agent pickup |
| `complexity`    | string (repeatable) | â€”           | Match any `complexity:*` value                |
| `effort`        | string (repeatable) | â€”           | Match any `effort:*` value                    |
| `judgement`     | string (repeatable) | â€”           | Match any `judgement:*` value                 |
| `limit`         | int                 | 500           | Maximum items returned (hard cap 2000)        |

**Response:**

```json
{
  "items": [
    {
      "repository": "D-sorganization/runner-dashboard",
      "number": 76,
      "title": "...",
      "url": "...",
      "author": "dieter",
      "assignees": [],
      "labels": ["bug", "ci"],
      "age_hours": 12.3,
      "taxonomy": {
        "type": "task",
        "complexity": "routine",
        "effort": "m",
        "judgement": "objective",
        "quick_win": false,
        "panel_review": false,
        "domains": ["backend"],
        "wave": 2
      },
      "agent_claim": null,
      "claim_expires_at": null,
      "linked_pr": null,
      "pickable": true,
      "pickable_blocked_by": []
    }
  ],
  "errors": []
}
```

**Taxonomy parsing** (`parse_taxonomy` in `issue_inventory.py`):
Labels take precedence. Recognised prefixes: `type:*`, `complexity:*`,
`effort:*`, `judgement:*`, `wave:*`, `domain:*`. Boolean flags: `quick-win`,
`panel-review`.

**Pickability rules** (`is_pickable` in `issue_inventory.py`):
An issue is pickable when ALL of the following hold:

1. `state == "open"`
2. No linked open PR (`linked_pr == null`)
3. No active `claim:*` label
4. `judgement` not in `{"design", "contested"}`

`pickable_blocked_by` lists the human-readable reasons when `pickable` is
`false`.

- Per-repo errors appear in `errors[]`; a failing repo does not abort the
  whole request.
- Responses are cached 30 seconds in-process.

### 12.2 Linear Inventory Module

Implemented in `backend/linear_inventory.py`. This is a backend inventory data
layer only; no `/api/linear/*` route, unified GitHub/Linear collapse layer, or
Linear webhook handling is exposed by this module.

`fetch_workspace_issues(workspace, mapping, client, state="open",
team_keys=None, limit=500)` fetches one configured Linear workspace through
`LinearClient.fetch_issues()`, applies `linear_taxonomy_map.apply_mapping()`,
and normalises each Linear issue into the same canonical shape returned by
`issue_inventory.py`, with additive `linear` metadata and `sources:
["linear"]`. Errors are returned as `(items=[], error="...")` instead of being
raised so callers can aggregate across workspaces.

`fetch_all_issues(config, client, state="open", pickable_only=False,
complexity=None, effort=None, judgement=None, limit=500)` gathers all
configured workspaces concurrently and returns `{"items": [...], "errors":
[...]}`. Filtering semantics mirror `GET /api/issues` for `pickable_only`,
`complexity`, `effort`, `judgement`, and `limit`; results are cached for the
same 30 second in-process TTL as GitHub issue inventory.

Linear normalisation rules:

- Linear state types `triage`, `backlog`, `unstarted`, and `started` map to
  canonical `state: "open"`; `completed` and `canceled` map to `"closed"`.
- `age_hours` uses the shared issue inventory age helper and Linear
  `createdAt`.
- Pickability uses the shared `issue_inventory.is_pickable()` rules. Linear
  items have no native `agent_claim` or `claim_expires_at`.
- Taxonomy comes from the Linear mapping result, excluding mapping-only
  `derived_labels` and `source_signals` keys from the canonical `taxonomy`
  object. The derived labels remain on the canonical `labels` field.
- GitHub issue URLs in `attachments.nodes[].url` are extracted into
  `linear.github_attachments`. When present, the first attachment also fills
  canonical `repository` and `number` for compatibility with existing issue
  consumers; Linear-only items use `repository: ""` and `number: null`.

---

## 13. Quick Dispatch API

### 13.1 Endpoint

`POST /api/agents/quick-dispatch`

Triggers the `Agent-Quick-Dispatch.yml` workflow in `Repository_Management` for
an ad-hoc agent task.

**Request body:**

```json
{
  "repository": "D-sorganization/runner-dashboard",
  "prompt": "Fix the failing test in test_api.py",
  "provider": "claude_code_cli",
  "model": "claude-opus-4-7",
  "ref": "main",
  "task_kind": "adhoc"
}
```

**Success response (200):**

```json
{
  "accepted": true,
  "envelope_id": "uuid-hex",
  "fingerprint": "sha256-prefix",
  "workflow_run_url": "https://github.com/.../actions",
  "history_id": "uuid-hex",
  "reason": ""
}
```

**Rejection response (409):**

```json
{ "accepted": false, "reason": "provider_unavailable: ..." }
```

### 13.2 Validation

- `prompt` must be at least 10 characters (400 if not).
- `provider` must exist in `PROVIDERS` and have `availability == "available"`.
  Rejected with `{"reason": "provider_unavailable: <detail>"}`.
- Provider must have `dispatch_mode == "github_actions"`.

### 13.3 Rate Limiting

10 calls per 60-second window per process (in-process token bucket).
Returns HTTP 429 `{"reason": "rate_limited", "retry_after_seconds": N}` when
exceeded.

### 13.4 Workflow Not Configured

If `Agent-Quick-Dispatch.yml` does not exist in `Repository_Management`, the
endpoint returns HTTP 501:

```json
{
  "reason": "workflow_not_configured",
  "suggested_workflow": "Agent-Quick-Dispatch.yml"
}
```

### 13.5 Audit Log

Every accepted dispatch writes a `DispatchAuditLogEntry`-shaped record to
`_QUICK_DISPATCH_HISTORY_PATH` (default:
`~/actions-runners/dashboard/quick_dispatch_history.json`). The path can be
overridden via the `QUICK_DISPATCH_HISTORY_PATH` environment variable.

### 13.6 Implementation

Core logic lives in `backend/quick_dispatch.py`. The server route at
`POST /api/agents/quick-dispatch` is a thin shell that calls
`quick_dispatch.quick_dispatch()`.

---

## 14. Bulk Dispatch API

### 14.1 PR Dispatch

`POST /api/prs/dispatch`

Dispatches agents to one or more pull requests via `Agent-PR-Action.yml`.

**Request body:**

```json
{
  "selection": {
    "mode": "single | repo | list | all",
    "repository": "D-sorganization/runner-dashboard",
    "number": 76,
    "items": [{ "repository": "...", "number": 1 }]
  },
  "provider": "claude_code_cli",
  "prompt": "Address review comments",
  "model": "claude-opus-4-7",
  "confirmation": { "approved_by": "dieter", "note": "manual click" }
}
```

**Response:**

```json
{
  "accepted": 5,
  "rejected": [{ "repository": "...", "number": 4, "reason": "..." }],
  "envelope_ids": ["uuid-hex"],
  "fingerprints": ["sha256-prefix"]
}
```

### 14.2 Issue Dispatch

`POST /api/issues/dispatch`

Same shape as PR dispatch, with two additional fields:

- `"force": true` â€” skip pickability enforcement (requires PRIVILEGED access).
  When forced, `forced: true` is recorded in the audit log.
- Pickability is enforced server-side: issues with `pickable=false` are rejected
  with `reason="not_pickable: <reason>"`.

### 14.3 Selection Modes

| Mode     | Description                                                |
| -------- | ---------------------------------------------------------- |
| `single` | One specific PR/issue by `repository` + `number`.          |
| `repo`   | All open PRs/issues in a repository (caller pre-resolves). |
| `list`   | Explicit list of `{repository, number}` items.             |
| `all`    | All pre-populated items. Hard-capped at 100 targets.       |

### 14.4 Concurrency

Fan-out dispatches run in parallel with an `asyncio.Semaphore` of 4.

### 14.5 Workflow Not Configured

If the target workflow file (`Agent-PR-Action.yml` or `Agent-Issue-Action.yml`)
does not exist in `Repository_Management`, the affected target is added to the
`rejected[]` list with `reason="workflow_not_configured: ..."`.

### 14.6 Audit Logs

- PR dispatches: `_PR_DISPATCH_HISTORY_PATH`
  (default `~/actions-runners/dashboard/pr_dispatch_history.json`,
  override via `PR_DISPATCH_HISTORY_PATH`).
- Issue dispatches: `_ISSUE_DISPATCH_HISTORY_PATH`
  (default `~/actions-runners/dashboard/issue_dispatch_history.json`,
  override via `ISSUE_DISPATCH_HISTORY_PATH`).

### 14.7 Dispatch Contract

Three new actions are registered in the `ALLOWLISTED_ACTIONS` catalog in
`backend/dispatch_contract.py`:

| Action                  | Access     | Requires Confirmation |
| ----------------------- | ---------- | --------------------- |
| `agents.dispatch.adhoc` | PRIVILEGED | Yes                   |
| `agents.dispatch.pr`    | PRIVILEGED | Yes                   |
| `agents.dispatch.issue` | PRIVILEGED | Yes                   |

### 14.8 Implementation

Core logic lives in `backend/agent_dispatch_router.py`. The server routes at
`POST /api/prs/dispatch` and `POST /api/issues/dispatch` are thin shells that
call `agent_dispatch_router.dispatch_to_prs()` and
`agent_dispatch_router.dispatch_to_issues()` respectively.

## 15. Fleet Node Security

### 15.1 Phase 1: Envelope Signing & Replay Protection

All fleet dispatch envelopes (`CommandEnvelope` objects) are cryptographically
signed with HMAC-SHA256 and include replay protection and timestamp validation
to prevent unauthorized command execution, tampering, and replay attacks.

### 15.2 Command Envelope Structure

Every dispatch envelope includes the following security fields:

| Field          | Type               | Purpose                                                           |
| -------------- | ------------------ | ----------------------------------------------------------------- |
| `envelope_id`  | UUID4 (string)     | Unique envelope identifier for replay detection                   |
| `signature`    | hex string         | HMAC-SHA256 signature of the canonical envelope payload           |
| `issued_at`    | ISO 8601 timestamp | Envelope creation time; must be within Â±5 minutes of server time |
| `requested_by` | string             | User/principal requesting the action                              |
| `action`       | string             | Allowlisted action name (e.g., `control.runner.start`)            |
| `payload`      | dict               | Action-specific parameters (e.g., runner ID)                      |

Approval of privileged actions includes:

| Field           | Type               | Purpose                                                  |
| --------------- | ------------------ | -------------------------------------------------------- |
| `approved_by`   | string             | User approving the action                                |
| `approved_at`   | ISO 8601 timestamp | Approval time; must be within Â±5 minutes of server time |
| `approval_hmac` | hex string         | HMAC-SHA256 signature binding approval to the envelope   |

### 15.3 Signature Validation

When a `CommandEnvelope` is created via `CommandEnvelope.from_dict()`, the
signature is verified against the envelope's canonical JSON payload using a
deployment-wide signing secret loaded from the `DISPATCH_SIGNING_SECRET`
environment variable (or `~/.config/runner-dashboard/dispatch_signing_key` if
not set).

Verification failure raises an exception; invalid envelopes never reach business
logic.

### 15.4 Timestamp Validation

Both `issued_at` and `approved_at` timestamps are validated to be:

1. Parseable ISO 8601 strings
2. Not more than 5 minutes in the past (freshness check)
3. Not more than 1 minute in the future (clock skew tolerance)

Validation result is a `TimestampValidationResult` enum: `VALID`, `TOO_OLD`,
or `CLOCK_SKEW`.

### 15.5 Replay Protection

Every processed envelope ID is stored in the `processed_envelopes` table with
a 24-hour TTL. The `/api/fleet/dispatch/submit` endpoint checks this table
before accepting an envelope. Duplicate envelope IDs are rejected with a 400
Bad Request response.

Expired entries are periodically cleaned up (currently at server startup).

### 15.6 Crypto Validation Route

The `/api/fleet/dispatch/submit` endpoint performs full crypto validation:

1. Parse the envelope from the request body
2. Verify the envelope signature via `validate_envelope_crypto()`
3. Check for replay via `_is_envelope_replay()`
4. Validate timestamp freshness
5. Record the envelope ID as processed
6. Proceed to business logic validation

If any crypto check fails, the endpoint returns 400 Bad Request with a
descriptive error (e.g., "Envelope has already been processed (replay
detected)").

### 15.7 Implementation Details

**Signing secret generation:**

```bash
# Generate a 48-byte (384-bit) random hex string
openssl rand -hex 24 > ~/.config/runner-dashboard/dispatch_signing_key
chmod 600 ~/.config/runner-dashboard/dispatch_signing_key
export DISPATCH_SIGNING_SECRET=$(cat ~/.config/runner-dashboard/dispatch_signing_key)
```

**Signing algorithm:**

- Canonical JSON of the envelope (with `signature` field omitted)
- HMAC-SHA256 with the deployment signing secret
- Hex-encoded result

**Signature binding:**

- CommandEnvelope.from_dict() auto-verifies the signature in `__post_init__`
- DispatchConfirmation.approval_hmac binds the approval to the envelope_id

**Database schema:**

```sql
CREATE TABLE processed_envelopes (
  envelope_id TEXT PRIMARY KEY,
  processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME
);
```

## 16. Assistant Sidebar

### 16.1 Overview

A persistent collapsible sidebar that provides a conversational AI assistant
interface accessible from any tab in the dashboard.

### 16.2 Toggle

A button labelled "â˜° Asst" in the header-right area toggles the sidebar
open or closed. The button is highlighted (blue background) when the sidebar
is open.

### 16.3 Layout

When open, the sidebar docks alongside the main content area in a flex row.
The user may configure it to dock to the left or right of the viewport.
The default position is right.

- Default width: 360px
- Draggable resize handle: 280px â€“ 600px range
- The main content shrinks to fill the remaining width

### 16.4 Persistence

All sidebar preferences are stored in `localStorage` under the `assistant:`
prefix:

| Key                        | Description                                                                                | Default   |
| -------------------------- | ------------------------------------------------------------------------------------------ | --------- |
| `assistant:open`           | Whether sidebar is currently open                                                          | `false`   |
| `assistant:position`       | Dock side (`"left"` or `"right"`)                                                          | `"right"` |
| `assistant:width`          | Sidebar width in pixels                                                                    | `360`     |
| `assistant:transcript`     | Conversation history, written only when history saving is enabled (capped at 200 messages) | `[]`      |
| `assistant:transcript:ts`  | Unix-ms timestamp used to expire saved conversation history after 24 hours                 | unset     |
| `assistant:saveHistory`    | Opt-in preference for saving assistant chat history                                        | `false`   |
| `assistant:openByDefault`  | Open automatically on load                                                                 | `false`   |
| `assistant:includeContext` | Send page context with each message                                                        | `true`    |

Assistant chat history is privacy-preserving by default: transcripts remain
in memory unless the operator enables the `Save chat history` control in the
sidebar header or settings panel. Disabling the control or using `Clear chat
history` removes both transcript storage keys immediately.

### 16.5 Conversation

Messages are displayed as chat bubbles. User messages dock right with a blue
background; assistant replies dock left with a tertiary background. Assistant
responses are rendered with a minimal inline Markdown renderer supporting
bold, italic, inline code, fenced code blocks, links, and ordered/unordered
lists â€” no external library required.

Input is a textarea. Enter sends the message; Shift+Enter inserts a newline.

### 16.6 API Integration

Messages are sent to `POST /api/help/chat` with the body:

```json
{
  "question": "<user message>",
  "page_context": {
    "tab": "<active tab name>",
    "url": "<window.location.href>",
    "selection": "<selected text, up to 500 chars>"
  }
}
```

`page_context` is omitted when the "Include page context" setting is disabled.

### 16.7 Settings

A gear icon in the sidebar header opens a settings card with:

- **Position**: radio buttons for Left / Right dock
- **Open by default**: checkbox
- **Include page context**: checkbox
- **Clear conversation**: destructive button that empties the transcript

### 16.8 Implementation

The `AssistantSidebar` component is defined in `frontend/src/legacy/App.tsx` just
before `QuickDispatchPopover`. It follows the legacy no-JSX convention
of the rest of the frontend. Open/closed state is owned by the `App` component
and passed down as props; all other sidebar state is internal.

## 16. Python Dependency Updates & Test Hardening

### 16.1 Pydantic Version Upgrade

**Updated:** `pydantic==2.10.6` â†’ `pydantic==2.13.3`

- Resolves compatibility issues with Python 3.14's PyO3 bindings
- Maintains backward compatibility with all existing request/response schemas
- No breaking changes to API contracts or validation behavior

### 16.2 API Integration Test Hardening

Tests in `tests/test_api_integration.py` now include required HTTP headers for
proper authentication and CSRF protection:

- **`Authorization: Bearer test-key`** â€” Satisfies FastAPI app's
  `DASHBOARD_API_KEY` import-time validation. The dashboard expects a valid
  Bearer token for authenticated routes.
- **`X-Requested-With: XMLHttpRequest`** â€” Standard CSRF protection header
  required for state-changing requests (PUT, POST, DELETE). This header signals
  to the dashboard that the request originated from the frontend JS, not from
  an HTML form cross-origin submission.

### 16.3 Test Results

**Before:** 158 passed, 8 failed, 1 xfailed  
**After:** 166 passed, 1 xfailed âœ“

The 8 previously failing tests required these headers:

- Tests on routes that validate Bearer tokens
- Tests on routes that enforce CSRF protection
- Tests that mock state-changing operations

All tests now pass consistently on Python 3.11, 3.12, and 3.13. Python 3.14
testing awaits environment availability.

## 17. PWA Native Launcher & Recovery Path (Issue #61)

### 17.1 Overview

The dashboard can be installed as a Progressive Web App (PWA) or Chrome app,
but browser sandboxing prevents direct execution of native processes. This
section documents the architecture for launching the backend and offering
recovery controls when the backend becomes unavailable.

**Design Principle:** Explicit operator intent, no silent auto-restart, all
recovery actions logged for audit.

### 17.2 Architecture: Custom URL Protocol Handler

**Recommended Approach:** Custom URL protocol handler (`runner-dashboard://start`)
with systemd/status-UI fallback.

**Platforms:**

- **Windows/macOS:** Custom protocol handler (one-time registration during setup)
- **Linux:** Systemd service auto-restart + status UI fallback

### 17.3 Components

#### 17.3.1 Backend Health Check Endpoint

New endpoint `GET /health` (no authentication required, internal localhost only):

```python
@router.get("/health", tags=["diagnostics"])
async def health_check() -> dict:
    """Launcher health check. Returns 200 if backend is ready."""
    return {
        "status": "ready",
        "timestamp": datetime.now(datetime.UTC).isoformat()
    }
```

Frontend polls this endpoint every 2 seconds. If no response for >5 seconds,
shows recovery modal.

#### 17.3.2 Launcher Script (Windows: `deploy/launcher.ps1`)

PowerShell script that handles `runner-dashboard://start` protocol:

1. Checks if backend is responding (HTTP health check)
2. If running, opens browser to `http://localhost:8321`
3. If not running, starts the backend service (via WSL/systemd)
4. Performs health check with exponential backoff (max 10 attempts)
5. On success, opens browser; on failure, logs error and exits with non-zero code
6. All actions logged to `~/.config/runner-dashboard/launcher.log`

**Usage from frontend:**

```html
<a href="runner-dashboard://start">Start Dashboard</a>
```

#### 17.3.3 Protocol Handler Registration (Windows: `deploy/register-protocol.ps1`)

PowerShell script that registers the custom protocol handler in Windows registry:

```powershell
# Creates registry entry:
# HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.runner-dashboard
# -> Points to launcher.ps1 as handler
```

Called once during `deploy/setup.sh` (Windows only). Requires operator to
approve the protocol handler in the browser (native OS dialog).

#### 17.3.4 Frontend Recovery UI Modal

When the health check fails:

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ Dashboard backend is not responding      â”‚
â”‚                                         â”‚
â”‚ [Start Now]  [Manual Instructions]     â”‚
â”‚                                         â”‚
â”‚ If you continue to see this error,      â”‚
â”‚ check ~/config/runner-dashboard/launcher.log
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

- **"Start Now"** (Windows/macOS): Triggers `runner-dashboard://start` protocol
- **"Manual Instructions"** (all platforms): Shows copy-paste terminal command
- **"Refresh"** (after action): Re-checks health and closes modal on success

### 17.4 Implementation Details

#### Health Check Polling

Frontend JavaScript (in main `App` component):

```javascript
// Poll /health every 2 seconds
const [backendHealthy, setBackendHealthy] = useState(true);
useEffect(() => {
  const interval = setInterval(async () => {
    try {
      const resp = await fetch("http://localhost:8321/health", {
        timeout: 3000,
      });
      setBackendHealthy(resp.ok);
    } catch (err) {
      setBackendHealthy(false);
    }
  }, 2000);
  return () => clearInterval(interval);
}, []);
```

#### Launcher Protocol Flow

1. Frontend detects backend down
2. Shows modal with "Start Now" button
3. Click â†’ `<a href="runner-dashboard://start">` (browser navigates)
4. Browser recognizes protocol, launches registered script
5. Launcher script starts backend, checks health, opens browser to dashboard
6. Modal auto-closes when health check succeeds
7. On Linux: manual instructions shown; operator runs `systemctl restart runner-dashboard`

### 17.5 Security Considerations

**Protocol Handler:**

- âœ… Only `runner-dashboard://` scheme (no collision with other apps)
- âœ… Script is local, operator-controlled, no network access
- âœ… Operator approves handler installation once during setup
- âœ… Browser prevents non-local sites from triggering the protocol
- âœ… Launcher script has hardcoded paths (no shell expansion)

**Health Endpoint:**

- âœ… No authentication required (internal localhost:8321 only)
- âœ… Returns minimal data (status + timestamp)
- âœ… No secrets or operational state exposed

**Recovery UI:**

- âœ… "Manual Instructions" path requires operator terminal use
- âœ… Protocol handler requires operator browser approval
- âœ… No automatic remediation; all actions explicit

### 17.6 Deployment

**During `deploy/setup.sh` (Windows):**

```bash
if [[ "$OS" == "Windows_NT" ]]; then
  powershell -ExecutionPolicy Bypass \
    -File deploy/register-protocol.ps1
fi
```

**Operator sees:** "Allow runner-dashboard to launch an app?" â†’ Click "Allow"

**Manual re-registration (if needed):**

```powershell
powershell -ExecutionPolicy Bypass -File deploy/register-protocol.ps1
```

### 17.7 Operator Documentation

See [`docs/pwa-launcher-design.md`](docs/pwa-launcher-design.md) for:

- Detailed architecture evaluation (Options 1â€“4)
- Implementation checklist
- Troubleshooting guide
- Platform-specific instructions (Windows/macOS/Linux)

### 17.8 Success Criteria

- âœ… "Start Now" button successfully starts backend and opens dashboard
- âœ… No manual terminal commands needed for happy path

---

## 18. Identity & Quotas (Wave 3)

### 18.1 Multi-User Identity Model

The dashboard uses a multi-principal identity model where every authenticated
request is attributed to a `Principal` (human or bot).

**Principal Model:**

- `id`: Unique identifier (e.g., `dashboard-operator`, `runner-bot`)
- `type`: `human` or `bot`
- `roles`: List of roles (e.g., `admin`, `operator`, `viewer`, `bot`)
- `quotas`: Resource limits (see below)

### 18.2 Resource Quotas (Fair Sharing)

Quotas prevent any single principal from monopolizing fleet resources or
depleting API budgets.

| Resource              | Default | Description                                                   |
| --------------------- | ------- | ------------------------------------------------------------- |
| `max_runners`         | 2       | Maximum concurrent runners leased by this principal           |
| `agent_spend_usd_day` | $10.00  | Maximum daily spend on paid agent dispatches ($0.10/dispatch) |
| `local_app_slots`     | 1       | Maximum local application slots                               |

**Enforcement:**

- **Dispatch check:** `quota_enforcement.py` validates remaining spend and
  runner slots before allowing a dispatch.
- **Bulk truncation:** Bulk PR/issue dispatches are automatically truncated
  to fit within the principal's remaining `max_runners` quota.

### 18.3 Runner Lease Management

The lease layer (`runner_lease.py`) tracks active claims on runners.

- **Lease types:** Physical (tied to a `runner-id`) or Virtual (tied to a
  `task-id` before a runner is assigned).
- **Lease Awareness:** The `runner_autoscaler.py` respects active leases; it
  will not stop a runner that holds a valid claim, even if it is idle.
- **Lease Reaper:** Stale leases are automatically cleared after 1 hour or
  upon task completion.
- **Unification:** Internal leases are synchronized with GitHub `claim:*`
  labels and `lease:` expiry comments found in issue/PR inventories via
  `lease_synchronizer.py`.

### 18.4 Onboarding & Principals Configuration

Principals are defined in `config/principals.yml`.

```yaml
principals:
  - id: dashboard-operator
    type: human
    roles: [admin]
    github_username: operator-login
```

New principals can be added by editing this file; the dashboard reloads it
automatically. Service tokens for bot principals can be minted via the
Identity Manager (`identity_manager.mint_service_token`).

<!-- spec-trigger-145 -->

### 18.6 CI Action Pinning, Workflow Concurrency Policy & Tool Version Parity (Issues #390, #689)

To prevent silent drift between local development and CI, the repository
enforces two invariants:

- **Single SHA per action:** every `actions/<name>@<sha>` reference in
  `.github/workflows/*.yml` must resolve to one 40-char SHA across all
  files, with one consistent `# vN` comment. The `verify-action-pin-uniformity`
  step in `ci-standard.yml` (job `ci-health-check`) enforces this, and
  `tests/test_workflow_action_pinning.py` provides a fast pytest guard.
- **Tool version parity:** `pyproject.toml [dependency-groups.dev]` pins
  `ruff` and `mypy` exactly (e.g. `ruff==0.14.10`, `mypy==1.13.0`) to
  match the `rev:` values in `.pre-commit-config.yaml`. The
  `verify-tool-version-parity` step in `ci-standard.yml` enforces this,
  preventing `uv sync` from installing a newer linter/type-checker than
  CI uses.
- **Workflow concurrency policy:** `config/workflow_concurrency_policy.json`
  is the single allowlist for PR-triggered workflows that intentionally keep
  `cancel-in-progress: false` and for repo-wide PR singleton concurrency
  groups. `tests/test_workflow_hygiene.py` and
  `.github/workflows/lint-workflow-files.yml` both enforce that PR workflows
  default to `cancel-in-progress: true`, include a PR/ref discriminator in
  their concurrency group unless explicitly allowlisted, and point operators
  to `docs/runbooks/ci-failure-triage.md` for the canonical remediation
  pattern.

### 18.5 Cross-Fleet Coherence & Admin API (Wave 4)

To ensure identity and quotas are respected across the entire fleet:

- **Cross-Node Principal Propagation**: The \CommandEnvelope\ in \dispatch_contract.py\ includes \principal\, \on_behalf_of\, and \correlation_id\. These fields are now included in the canonical JSON payload used to generate the HMAC-SHA256 signature, ensuring that malicious actors cannot forge identities during cross-node dispatch.
- **Hub-Side Merged Audit View**: A new endpoint \/api/fleet/audit\ aggregates orchestration audit logs from all nodes in the \FLEET_NODES\ configuration. It supports filtering by \principal\ and merges entries sorted by timestamp. Local audit logs can be retrieved via \/api/audit\.
- **Admin API**: The \/api/admin/\*\ router provides endpoints for managing the identity system:
  - \GET /api/admin/principals\: List all registered principals and their quotas.
  - \GET /api/admin/tokens\: List all active service token hashes.
  - \POST /api/admin/principals/{id}/token\: Mint a new service token for a bot principal.
  - \DELETE /api/admin/tokens/{token_hash}\: Revoke a service token.
  - \PATCH /api/admin/principals/{id}/quota\: Update quotas (\max_runners\, \gent_spend_usd_day\, \local_app_slots\) for a specific principal.
    < ! - -   U p d a t e d :   2 0 2 6 - 0 4 - 2 9 T 1 8 : 3 8 : 1 6   - - > 
     
     

### 18.6 Consistent Error Envelope (issue #406)

All 4xx and 5xx responses from `/api/*` routes return a JSON object conforming
to `ErrorResponse` (`backend/error_models.py`):

```json
{
  "error": "<machine-readable code>",
  "detail": "<human-readable description>",
  "request_id": "<optional trace id>"
}
```

Standard error codes:

| Code               | HTTP status | Meaning                               |
| ------------------ | ----------- | ------------------------------------- |
| `not_found`        | 404         | Resource does not exist               |
| `forbidden`        | 403         | Permission denied                     |
| `validation_error` | 422         | Invalid request input                 |
| `rate_limited`     | 429         | GitHub rate limit hit                 |
| `conflict`         | 409         | State conflict (e.g. already stopped) |
| `bad_gateway`      | 502         | Upstream GitHub API error             |
| `server_error`     | 500         | Internal server error                 |
| `service_error`    | 500/404/403 | systemd service lifecycle failure     |

Service lifecycle failures (`start`, `stop`, `restart`) additionally map
stderr text to semantic status codes via `service_stderr_to_status()`:

- "not loaded" / "Unit not found" → 404
- "permission denied" / "access denied" → 403
- anything else → 500

### 18.7 Typed GitHub Payload Models (issue #407)

GitHub API response dicts are now parsed at the boundary into typed Pydantic
view-models defined in `backend/models/github_payloads.py`:

| Model           | Replaces                                                                |
| --------------- | ----------------------------------------------------------------------- |
| `GhWorkflowRun` | `run.get("id")`, `(run.get("repository") or {}).get("name", "")` chains |
| `GhJob`         | `j.get("runner_name")`, label dicts vs strings                          |
| `GhRunner`      | `runner["labels"][i]["name"]`, `runner.get("busy")`                     |
| `GhRepository`  | nested repository sub-dict                                              |
| `GhActor`       | `triggering_actor.get("login")`                                         |

All models use `extra="ignore"` so new GitHub API fields never break
existing handlers. Handlers receive flat, typed objects (Law of Demeter).

### 18.8 Pooled GitHub API Client (issue #352)

A new `backend/gh_client.py` module replaces the hottest
`subprocess.run(["gh", "api", ...])` call-sites with a single pooled
`httpx.AsyncClient` that reuses TLS connections and caches the Bearer token.

**Key design:**

- Token loaded once from `GH_TOKEN` / `GITHUB_TOKEN` and cached in memory.
- Typed exceptions: `GhAuthError`, `GhRateLimited`, `GhNotFound`, `GhServerError`.
- `paginate(path)` async iterator follows GitHub Link headers automatically.
- `gh` CLI subprocess retained as fallback when token is absent.
- `gh_utils.gh_api()` delegates to `gh_client.get()` transparently; all
  existing call-sites continue to work without changes.

### 18.9 Log Shipping: Vector Sidecar + Retention Policy (issue #418)

Log aggregation sidecar configuration for the runner-dashboard fleet.

**Files added:**

- `deploy/observability/vector.toml` — Vector config shipping journald +
  Docker container logs to Loki; 7-day retention for app logs, 30-day for errors
- `deploy/observability/journald-retention.conf` — journald drop-in limiting
  on-host storage to 1 GB / 30 days
- `docker/docker-compose.yml` — Dashboard + Vector sidecar as a Compose stack;
  Docker json-file driver rotates at 100 MB × 7 files
- `docs/runbooks/log-retention.md` — Ops runbook covering setup, verification,
  troubleshooting, and Grafana alert definitions

**Retention tiers:**

| Tier                       | Storage          | Retention          |
| -------------------------- | ---------------- | ------------------ |
| info/warn application logs | Loki             | 7 days             |
| error/critical logs        | Loki             | 30 days            |
| Journald on-host           | systemd-journald | 1 GB max / 30 days |
| Docker json-file           | local            | 7 × 100 MB         |

## Security Fixes (issues #315, #317, #318)

### Auth loopback bypass (issue #315)

The loopback bypass that granted automatic admin access to requests from
127.0.0.1 or ::1 is now gated on the environment variable
DASHBOARD_LOOPBACK_AUTH=1. This variable must never be set in production;
it is intended solely for local single-user development where the dashboard
is not reachable beyond the loopback interface.

### HMAC payload signing (issue #317)

The dispatch envelope HMAC signature now includes a SHA-256 hash of the
payload field (payload_hash). This prevents capture-and-replay attacks
where an attacker captures a valid signed envelope and replays it with a
different payload. All new envelopes are signed with payload_hash in the
canonical JSON; the signing secret is DISPATCH_SIGNING_SECRET.

### approval_hmac binding (issue #318)

DispatchConfirmation.approval_hmac must be bound to the specific
envelope_id and action of the request it approves. The canonical
HMAC message is approve:<envelope_id>:<action>. If approval_hmac is
present and invalid, validate_envelope_crypto fails closed with
valid=False. An absent approval_hmac is accepted with a deprecation
warning for backwards-compatibility; clients should supply it.

### Autoscaler overload detection and systemd watchdog (PR #918)

`_default_pool_overloaded()` and `_default_pool_recovered()` are now
pure functions in `runner_autoscaler.py`, extracted from the inline
poll-loop expressions. This makes the overload contract unit-testable
without running the poll loop or touching systemd (OGLaptop 2026-06-09
regression: idle host at load 0.23/20 cores was wrongly scaled to the
floor and logged as "host overloaded").

The systemd watchdog path no longer requires the optional `systemd`
Python package. `_sd_notify_socket()` writes the sd_notify datagram
directly to `$NOTIFY_SOCKET`; `_notify_systemd()` tries the binding
first and falls back to the socket path. `NotifyAccess=main` is added
to `deploy/runner-autoscaler.service` so systemd honours keep-alive
pings from the main process.

`_stop_unit()` now accepts a `reason=` keyword argument so a
scheduled-surplus trim is logged as such rather than "host overloaded".
