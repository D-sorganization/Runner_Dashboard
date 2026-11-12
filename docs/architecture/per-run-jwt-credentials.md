# Per-run JWT credentials sequencing contract (Paperclip panel #3874 / issue #3879)

**Status:** Proposed — implementation tracked as a follow-up PR against this same issue (#3879, phase B).
**Authors:** RD Paperclip vs RD expert panel (2026-11-02), filed after panel consensus 4-0.

## Problem

`backend/agent_principal_defaults.py` ingests static, long-lived bearer tokens
from `agent-tokens/*.token` files (or overlay-declared paths) into
`IdentityManager.tokens` at startup. Each token is a permanent credential for
one agent principal: it has no `run_id` scope, and revoking it means editing
the token file and restarting the process that owns `IdentityManager`. A
leaked static token is valid indefinitely for every run that principal ever
starts.

This issue replaces that model with **short-lived, per-run JWTs**, minted at
run start and scoped to a single run. Static tokens keep working throughout
the migration (no mid-migration lockout) and are only removed after a
separate security/infra sign-off on the cutover.

### Existing primitives this design reuses (DRY)

- `backend.identity.IdentityManager.resolve_bearer_token` already does
  hash-lookup, expiry-check, and principal-resolution for a bearer token
  without ever handling or logging the raw secret — this stays the
  verification entry point; a JWT is not a second auth path.
- `IdentityManager.add_ephemeral_token` / `revoke_ephemeral_principal`
  (#1310) already provide in-memory-only, non-persisted, immediately
  revocable tokens bound to an ephemeral principal. Per-run JWTs are minted
  as ephemeral tokens through this existing mechanism, not a new store.
- `backend.github_app_auth.build_app_jwt` and
  `backend.cloudflare_access.CloudflareAccessValidator` are the repo's two
  existing JWT precedents: short `exp` windows, `aud`/`iss` pinned at
  verification time, and PyJWT (already a pinned dependency,
  `pyjwt==2.15.0`) for both encode and decode. This design follows the same
  shape rather than introducing a second JWT convention.

## Design goals

1. **No mid-migration lockout.** Static `agent-tokens/*.token` ingestion is
   untouched until a separate, explicit cutover PR removes it.
2. **One run, one credential.** A JWT authenticates exactly one run of one
   agent principal; it must not be replayable as a general-purpose bearer
   token for that principal once the run ends.
3. **Revocable before natural expiry.** A run that fails, is cancelled, or is
   flagged compromised must have its credential killed immediately, not just
   time out.
4. **Never log or persist the raw bearer.** Matches the existing
   `resolve_bearer_token` contract and `AGENTS.md` secret-hygiene rule; only a
   hash plus metadata is ever stored.
5. **No production authority change from this contract PR.** This PR defines
   the contract only; verification of real JWTs on real requests, and
   disabling static-token fallback, are separate, explicitly gated PRs.

## Principal

- `sub` (subject) is the **existing** `Principal.id` from
  `config/agent_principals.yml` / `agent-tokens/*.token` ingestion (e.g.
  `d-sorgclaudeagent`). A JWT does not mint a new identity or a new set of
  roles/scopes — authorization (`roles`, `scopes`, `quotas`) stays owned by
  the existing `Principal` record, looked up by `sub` exactly as
  `resolve_bearer_token` does today for static tokens.
- `run_id` (custom claim, required, non-empty string) scopes the token to one
  run. It is opaque to authorization decisions; it exists for audit
  (`TokenRecord.name` equivalent) and for targeted revocation without
  affecting the principal's other concurrent runs.
- `jti` (JWT ID, required, unique per mint) is the revocation handle: never
  the `run_id` itself, since one run may legitimately mint a replacement
  token (see Revocation).

## Audience

- `aud` is the **minting hub's canonical public origin** (the same value
  `fleet_public_origin.resolve_unique_hub_public_origin` resolves for that
  node), not a fixed string constant. A token minted by one hub must fail
  verification on a different hub or spoke — mirrors
  `CloudflareAccessValidator`'s `audience` pin, which already treats a
  mismatched `aud` as a hard verification failure, not a warning.
- `iss` is the fixed string `"runner-dashboard-agent-run"` so this token
  family is distinguishable from the unrelated GitHub App JWTs
  (`build_app_jwt`) and Cloudflare Access assertions that also flow through
  this process.

## Expiry

- `exp` defaults to **15 minutes** from mint, configurable per role via the
  same `Quota`-style per-principal override point `Principal.quotas` already
  uses for other per-agent limits (implementation PR decides the exact field
  name; this contract only fixes the default and the override mechanism
  shape).
- A run that needs more time **re-mints** a fresh JWT (new `jti`, new `exp`)
  through the same mint path rather than extending `exp` on an existing
  token — a signed JWT's `exp` cannot be mutated in place without
  invalidating the signature, so "renewal" is always "mint a replacement and
  let the old `jti` expire or be revoked."
- `iat` and `nbf` are both set to mint time; no backdating, no grace window
  before `nbf`.

## Revocation

JWTs are stateless by construction, so revocation is **not** "the signature
stops validating" — it is a server-side deny-check the verifier must run
before trusting a syntactically-valid, unexpired JWT:

1. At mint time, the hub registers `sha256(raw_jwt)` via the existing
   `IdentityManager.add_ephemeral_token(token_hash, principal, expires_at,
   name=f"run:{run_id}")` — no new storage table. `expires_at` mirrors the
   JWT's own `exp` so the two can never drift.
2. Verification always calls `resolve_bearer_token` (unchanged) *in addition
   to* checking the JWT's signature/`aud`/`iss`/`exp`/`nbf`. A syntactically
   valid JWT whose hash is not (or no longer) in `_ephemeral_tokens` is
   rejected — this is what makes early revocation possible at all, since the
   signature alone would otherwise still verify until `exp`.
3. Immediate revocation on run end (success, failure, or cancellation) calls
   the same hash-targeted removal `revoke_ephemeral_principal` already uses
   internally, rather than waiting for `exp`. The implementation PR adds a
   `revoke_ephemeral_token(token_hash)` sibling for this (today
   `revoke_ephemeral_principal` revokes *all* of a principal's ephemeral
   tokens, which is too broad when a principal has more than one run live).
4. A token that is never explicitly revoked still self-expires at `exp`
   (hub) and is also dropped from `_ephemeral_tokens` (in-memory; no
   persisted revocation list to prune).

## Storage and logging rules

- **Never** store or log the raw JWT. Only `sha256(raw_jwt)` (the existing
  `token_hash` shape), `principal_id`, `run_id`, `created_at`, and
  `expires_at` are retained, and only in memory (`_ephemeral_tokens`) — never
  written to `tokens.yml` or any coordination/event-bus payload.
- **Never** log the signing secret. The signing key is a long-lived
  credential of the *hub process*, not of any agent; it is supplied the same
  way `GITHUB_APP_PRIVATE_KEY[_FILE]` is today (env var or file path env var,
  never committed, never in `config/agent_principals.yml`).
- Mint and verification failures log `reason` codes only (matching
  `resolve_bearer_token`'s existing `"token_expired"` /
  `"token_hash_not_registered"` style), never the attempted token value or
  signing material.
- Audit trail for "which run used which credential" is the `run_id` claim
  plus the existing coordination/job-board events for that run — this
  contract does not add a second, overlapping audit log.

## Migration sequencing (contract only; implementation is phase B)

1. **This PR** — contract only; no code changes to `identity.py`,
   `agent_principal_defaults.py`, or any router. Nothing in production
   behavior changes.
2. **Mint path PR(s)** — add the JWT-mint endpoint/helper (hub-only, mirrors
   `fleet_run_token_service.mint_fleet_run_github_token`'s node/role/repo
   validation shape for request-time checks) and the `revoke_ephemeral_token`
   helper. Static `agent-tokens/*.token` ingestion is untouched; both
   credential kinds resolve through the same unchanged
   `resolve_bearer_token` path.
3. **Verification-wiring PR(s)** — have run-launching call sites request and
   use a per-run JWT instead of a static token, behind a flag/staged
   rollout. Static tokens remain valid for any caller that has not migrated.
   In-repo slice: ``clients/fleet/fleet_run_jwt.py`` (library) and
   ``scripts/exchange_run_jwt.py`` (CLI) exchange a bootstrap bearer
   for ``POST /api/fleet/run-jwt``; ``scripts/barb-worker/with-fleet-env.sh``
   performs that exchange when ``RUN_JWT_AGENT_SESSION=1`` (default off) and
   ``FLEET_RUN_JWT_RUN_ID`` + ``FLEET_RUN_JWT_CALLER_NODE`` are set. Hub-local
   agent sessions still need an owner decision on ``X-Fleet-Node`` / bootstrap
   auth before cutover.
4. **Separate security/infra sign-off issue** — only after 2 and 3 are
   merged and observed in staging does a dedicated issue retire static
   `agent-tokens/*.token` ingestion. This contract does not authorize that
   removal.

## Non-goals

- This contract does not change `Principal.roles` / `Principal.scopes`
  semantics, the `agent_principals.yml` schema, or any existing static-token
  behavior.
- This contract does not implement the SQLite WAL lease store (#3878) — that
  issue is sequenced after #3874's dual-read/drain harness (#3877), which is
  independent of this credential work.
- This contract does not specify a production cutover date or disable any
  existing auth path; see Migration sequencing above.
