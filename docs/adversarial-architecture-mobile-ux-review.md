# Runner Dashboard adversarial architecture and mobile UX review

**Snapshot:** 2026-11-01, source commit [`b478f628`](https://github.com/D-sorganization/Runner_Dashboard/commit/b478f628b4e2569372055f46e875cccea67f18ee). GitHub issue and pull-request states were checked at 2026-11-01 17:16 UTC. This is a source and public-tracker review, not a production assessment.

## Scope and evidence labels

This packet follows the requested themes: phone/Tailscale access and notifications, Agent Bus, resource health, distributed persona dispatch, shared memory, private sessions, librarian provenance, and Obsidian. Findings cite repository code or contracts. Tracker status is linked separately.

- **Implemented in source** means code, tests, or repository contracts exist. It does not establish that a service is deployed or configured.
- **Open work** means an issue/PR is open in the tracker snapshot. Open status is not proof that a feature is absent or incomplete; read the linked acceptance scope.
- **Live observation** means a direct observation of a running deployment or physical device. None was performed for this packet.
- **Unverified device acceptance** means no iOS/Android hardware run, Tailscale phone sign-in, installed PWA, push receipt, or offline recovery was observed.

## Executive assessment

The codebase has substantial foundations: scoped tailnet identity, explicit mobile layout contracts, browser push subscriptions, a durable SQLite Agent Bus, bounded shared-memory context, role-scoped lesson review, and a read-only Obsidian exporter. The main risks are at the boundaries between those pieces: runtime configuration is not established by source; push is best-effort; some generic memory supersession is not compare-and-swap; a local Obsidian hash is not an authenticity proof; and the repository's own mobile roadmap still requires device and offline acceptance.

The safest next steps are to measure production configuration and device behavior without exposing private data, give critical notifications durable retry semantics, close or explicitly constrain the generic-memory supersession race, and finish the Obsidian freshness slice before describing the projection as current. Keep private-mode controls feature-flagged until the threat-model gates are actually satisfied.

## Findings

### P1 — Critical push events have no durable delivery record or retry contract

`notify()` creates an in-memory task when called inside an event loop; failures are logged after completion. There is no persistent outbox, retry schedule, or delivery receipt in this path ([`backend/push.py`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/push.py#L338-L367)). A process restart can therefore lose a scheduled event, and callers cannot distinguish “queued” from “delivered.” This matters for agent completion, auth-expiry, or safety notifications if operators treat push as an alert channel.

**Abuse/failure scenario:** a worker completes a consequential action, the process exits after scheduling push but before the push service accepts it, and the operator assumes the phone alert is reliable.

**Recommendation:** classify push topics by consequence. For topics that require reliable follow-through, persist an outbox event with a stable idempotency key, bounded retry/backoff, terminal failure status, and a dashboard-visible receipt. Keep ordinary informational pushes explicitly best-effort. Do not put sensitive memory or thread text in lock-screen payloads by default.

**Acceptance:** kill the process after event persistence but before send; restart and verify one eventual delivery; force a permanent provider failure and verify visible terminal status; prove duplicate retries do not create duplicate operator actions; verify lock-screen payloads contain no private body text.

### P1 — Generic memory supersession can race

The authority/provenance contract records that generic `AssistantMemoryStore.append()` updates a supersession link with a best-effort conditional update, not atomically with insertion; two concurrent writers can produce ambiguous successor history. The same document distinguishes the genuinely transactional digest idempotency path and the lesson-review CAS path ([`docs/operations/obsidian-ro1-authority-provenance.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/operations/obsidian-ro1-authority-provenance.md#L19-L21)).

**Abuse/failure scenario:** two agents concurrently replace one decision or task; each believes its entry is the current successor and a later export or prompt reads conflicting “current” state.

**Recommendation:** make generic supersession atomic or reject concurrent replacement with an explicit conflict. If the product instead accepts branching history, expose that state as a conflict and prevent downstream “current” summaries from silently choosing one branch.

**Acceptance:** a deterministic concurrent-writer test proves exactly one successor wins, or that both are represented as a conflict; stale updates fail visibly; retries are idempotent; consumers never infer deletion or a lifted directive from a missing row.

### P1 — Tailscale phone authentication is conditional; deployed activation is unknown

The source admits a Tailscale identity only when the feature is enabled and the transport peer, resolved tailnet address, and allow-listed login checks pass ([`backend/tailnet_identity.py`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/tailnet_identity.py#L205-L244)). This is a useful boundary in code, but the repository snapshot cannot show whether the production variables, reverse proxy path, DNS, or phone client are configured correctly.

**Abuse/failure scenario:** an operator sees a documented Tailscale sign-in path and assumes it is active; a missing setting leaves the phone locked out, or an incorrect proxy/header path is misdiagnosed as an app defect.

**Recommendation:** keep a redacted, owner-run readiness check that reports enabled/disabled, configured allow-list count, and proxy/WhoIs health without displaying identities or credentials. Keep the current exact allow-list and transport checks; do not broaden a failed phone login into anonymous tailnet access.

**Acceptance:** on an enrolled phone, verify allowed identity succeeds, unlisted identity fails, forged headers from direct traffic fail, loopback and remote-device roles remain distinct, and unset configuration fails closed. Record device/OS/browser and timestamp; do not include account identifiers in public artifacts.

### P1 — Mobile roadmap still requires physical-device and offline acceptance

The mobile contract defines breakpoints and touch targets ([`docs/mobile-design-system.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/mobile-design-system.md#L21-L40)); the native-shell roadmap requires PWA install, offline snapshots, push, auth gates, viewport checks, and iOS/Android hardware qualification before packaging ([`docs/mobile-native-shell.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/mobile-native-shell.md#L28-L52)). Static viewport tests cannot establish installation prompts, browser background behavior, screen-reader interactions, notification delivery, or recovery after network loss.

**Abuse/failure scenario:** a cached screen looks healthy while stale; the operator submits a mutation offline and cannot tell whether it was sent, retained, or duplicated after reconnection.

**Recommendation:** use a small, repeatable device matrix before a mobile readiness claim. Show snapshot age and source on cached views; make every offline mutation visibly pending or unavailable; never silently retry a non-idempotent action.

**Acceptance:** on current supported iOS Safari and Android Chrome, install/open, authenticate over Tailscale, navigate at 375×812 and 412×915, receive a deep-linked push, disconnect/reconnect, verify snapshot age and mutation state, and confirm logout/token expiry. Capture sanitized evidence and separately label browser automation versus physical-device runs.

### P2 — Agent Bus persistence and leases do not prove multi-node writer safety

The Agent Bus stores principals, inbox messages, and tracked claims in SQLite with WAL and a process-local `RLock` ([`backend/agent_bus/store.py`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/agent_bus/store.py#L40-L75)). Messages carry idempotency keys and registration has bounded leases ([`backend/agent_bus/models.py`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/agent_bus/models.py#L20-L78)). These are sound single-service building blocks; the source does not establish that multiple dashboard nodes can safely write the same store or that failover preserves one authority.

**Abuse/failure scenario:** two active nodes accept conflicting writes during failover or share a filesystem whose locking semantics differ from the tested local SQLite setup; a lease or idempotency record is not globally authoritative.

**Recommendation:** document the supported deployment topology and keep one write authority unless cross-node consistency is demonstrated. Qualify failover with duplicate delivery, expired session, replay, and split-brain tests. Treat the UI's “online” state as transport health, not proof that a remote write committed.

**Acceptance:** two independent processes against the supported storage topology contend on one idempotency key and lease; exactly one durable message/claim is observed; failover cannot mint two active authorities; UI distinguishes accepted, pending, expired, and failed.

### P2 — Resource-health evidence is fragmented and partly operator-configured

The app has component health probes, including library, pool, memory, and context-pack checks (`backend/context_health.py`), and `/readyz` aggregates configured readiness probes (`backend/health.py`). The observability guide still says the central Prometheus scraper is not shipped here and asks the operator to confirm its scrape setup ([`docs/observability.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/observability.md#L122-L132)). Therefore source-level health endpoints do not establish fleet-wide freshness, alert coverage, or resource headroom on phones or nodes.

**Abuse/failure scenario:** a node reports API readiness while a stale or unreachable downstream store, remote runner, or metrics path is not represented in the screen the operator relies on.

**Recommendation:** present each signal with source, sampled-at time, freshness threshold, and a distinct unavailable state. Separate process readiness from runner, storage, network, and notification health. Do not label stale cached data “healthy.”

**Acceptance:** inject timeout, stale timestamp, partial source failure, and recovery for each displayed signal; verify a failed refresh does not advance last-success time and the UI exposes which source is unavailable. Validate alert delivery independently from metric emission.

### P2 — Obsidian export detects edits but cannot authenticate origin

The exporter has a strict entry/field allow-list, bounds, path confinement, and read-only store access. Its manifest uses unkeyed SHA-256; the module explicitly states that replacing both the Markdown and manifest can produce a matching hash ([`backend/staff/obsidian_export.py`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/staff/obsidian_export.py#L1-L20), [manifest and verification](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/staff/obsidian_export.py#L226-L305)). This is tamper/change detection, not a signed provenance chain.

**Abuse/failure scenario:** a consumer treats a valid hash as proof that a note came from the dashboard, although an actor who can edit the export directory can replace both files.

**Recommendation:** retain the warning in every consumer view. If provenance must survive an untrusted filesystem, add a separately reviewed signing/verification trust root and key lifecycle; otherwise constrain the claim to “unchanged relative to this manifest.” Never add write-back or promote imported Markdown to instructions.

**Acceptance:** mutate only the Markdown and verify rejection; replace Markdown and manifest together and verify the UI still labels the pair unauthenticated; reject symlink escapes; prove export invokes no store mutation; keep annotations separate from regenerated output.

### P2 — Obsidian freshness/offline slice remains a separate acceptance gate

The parent epic requires explicit source class/version, capture time and age, stale/offline/unavailable states, no fallback to local memory, and recovery that does not overwrite annotations. Its checked-in tracker text records RO-3 as retained scope and says no separate issue was created because that external write needs owner approval. In the current tracker snapshot, parent issue [#3505](https://github.com/D-sorganization/Runner_Dashboard/issues/3505) is **open**; implementation slices [#3506](https://github.com/D-sorganization/Runner_Dashboard/issues/3506) and [#3507](https://github.com/D-sorganization/Runner_Dashboard/issues/3507) are **closed**. The source contract is therefore not evidence that the freshness view or live topology has been accepted.

**Abuse/failure scenario:** a user opens an old generated note after a refresh failure and mistakes it for current canonical memory, or assumes a deleted note means a directive was retired.

**Recommendation:** complete the RO-3 gate only through its authorized tracking path. Keep generated files read-only projections; show “last successful capture” and age; make missing rows non-authoritative; preserve human annotations on regeneration.

**Acceptance:** fake-clock tests cover never fetched, fresh, stale, offline, unavailable, partial, invalid provenance, tampering, and recovery. Failed refresh must not advance capture time; no local DB fallback or upstream write occurs; recovery replaces generated output and preserves annotations.

### P2 — Private Mode documentation is not a production privacy guarantee

The open epic [#3249](https://github.com/D-sorganization/Runner_Dashboard/issues/3249) is feature-flagged. The threat model explicitly says runtime sink adapters, encrypted transcripts, private CI control, identity, key management, audit, and separation of duties belong to later slices; it warns that enabling the flag before those controls land provides no protection ([`docs/architecture/private-mode-threat-model.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/architecture/private-mode-threat-model.md#L51-L68)). The generic policy schema is illustrative and keeps operator policy out of public core ([`docs/architecture/private-mode-policy-schema.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/architecture/private-mode-policy-schema.md#L8-L47)).

**Abuse/failure scenario:** an operator sees `enabled: true` and assumes sensitive content is isolated even though a downstream sink does not yet enforce classification.

**Recommendation:** retain the off-by-default behavior and block any operator-facing “protected” status until every required sink and audit path is verified. Test policy downgrade, unknown repository, cache, log, transcript, artifact, push, memory, and export paths as one end-to-end boundary.

**Acceptance:** each sink has a classification-aware denial test and sanitized audit evidence; flag-off compatibility is exact; unknown/malformed policy fails closed; production activation has an explicit owner decision and a measured end-to-end canary.

### P3 — Shared-memory context is bounded, but source freshness is not the same as authority

The shared-memory bridge requires the caller's `memory.read`, uses a separate read-only service token, applies a deadline and size caps, labels context as untrusted reference, and does not fall back to local memory ([`backend/staff/chat_shared_memory.py`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/backend/staff/chat_shared_memory.py#L89-L179)). The shared-memory guide says automatic summaries are opt-in ([`docs/agents/barb-shared-memory.md`](https://github.com/D-sorganization/Runner_Dashboard/blob/b478f628b4e2569372055f46e875cccea67f18ee/docs/agents/barb-shared-memory.md#L20-L39)). A fetched timestamp proves when the prompt read occurred, not that a directive is still current or that a source was authoritative.

**Abuse/failure scenario:** malicious or superseded memory text contains instructions; a model follows it as policy, or a stale directive is treated as current after a source outage.

**Recommendation:** preserve the instruction/data boundary. Render source status, capture time, and review state; require live authoritative reads for holds and permissions. Keep summaries opt-in and use the reviewed lesson lifecycle for durable lessons.

**Acceptance:** prompt-injection fixtures remain inert; unavailable source is explicit; stale entries are labeled; holds/directives are resolved only from authoritative APIs; no write token is used for context reads.

## Cross-cutting threat scenarios

| Scenario | Existing boundary | Residual check |
| --- | --- | --- |
| Forged Tailscale identity header | Raw transport peer and allowed-login checks in `tailnet_identity.py` | Test the deployed proxy chain on real phone and direct-tailnet traffic; source tests cannot prove its headers/configuration. |
| Lost or duplicated push | Provider response handling, quiet hours, stale-subscription purge | Durable outbox, retry/idempotency, receipt, and lock-screen minimization for critical events. |
| Prompt injection in memory or Markdown | Shared context is marked untrusted; exporter fences text and allow-lists fields | Verify every consuming prompt and UI preserves the data boundary; generated content never becomes an instruction source. |
| Stale or forked authority | Lesson review has status transitions; digest idempotency is transactional | Generic supersession race and offline/failover freshness need explicit conflict states. |
| Private-data spill to shared systems | Private Mode threat model names logs, transcripts, panels, boards, metrics, CI, and artifacts | Prove all sinks enforce classification before enabling the flag. |
| Stale health shown as live | Readiness and context-health probes exist | Carry sample time and source through aggregation; test stale, partial, and recovery states. |

## Prioritized follow-up and measurable gates

1. **P1, alert reliability:** introduce durable retry/receipt behavior for only the topics the owner defines as operationally critical. Gate: restart/retry and duplicate-delivery tests above pass.
2. **P1, memory authority:** resolve or explicitly surface generic supersession conflicts. Gate: deterministic concurrent-update test and consumer conflict behavior pass.
3. **P1, phone/mobile acceptance:** execute the roadmap's real iOS/Android install, auth, viewport, push, offline, and recovery matrix. Gate: sanitized device/OS/browser evidence; no source-only substitute.
4. **P2, state and resource visibility:** qualify supported Agent Bus topology and show per-signal freshness/unavailable states. Gate: two-process contention and stale/partial-source tests pass.
5. **P2, Obsidian:** finish freshness-state acceptance after the closed RO-1/RO-2 prerequisites, using only the approved tracking path. Gate: fake-clock state table, zero-write proof, and annotation-preserving recovery.
6. **P2, Private Mode:** keep disabled until all later slices enforce the classification at every sink. Gate: full boundary matrix plus owner activation decision.

## Live observations and limits

- GitHub issue/PR status was queried live; links above identify the tracker objects. The board and issue histories can be incomplete, so absence of an issue or PR is not asserted as proof that no work exists.
- No production host, staff endpoint, metrics backend, agent session, Obsidian vault, or Tailscale device was contacted. No notification was sent and no deployment behavior is claimed.
- No iOS or Android device was available to this review. PWA install, hardware accessibility, real push receipt, background suspension, and offline recovery remain **unverified**.
- Findings are grounded in source at commit `b478f628`; later main changes may alter them.
