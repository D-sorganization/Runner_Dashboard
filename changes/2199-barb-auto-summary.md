---
issue: 2199
summary: "Add opt-in canonical Barb conversation summaries with confirmed save status"
dl_state: "in_review"
next_step: "Merge the PR, deploy the qualified digest API to hub-a, then activate and verify summaries."
branch: "feat/barb-auto-summary-2199"
---

## Added

- Opt-in automatic short Barb continuity summaries use the configured canonical shared-memory origin, the original caller's `memory.shared.summarize` permission and the digest-only idempotent API. Secrets are redacted; raw transcripts are never used as a fallback digest.
- Dashboard messages show whether a summary was saved, already present, omitted or unavailable. Failed memory writes preserve the completed reply and never substitute node-local memory.

## Handoff

- Requires the session-digest API release on the canonical hub-a origin, a dedicated service token with `memory.read` plus `memory.shared.summarize` (no general `memory.write`), and `BARB_SHARED_MEMORY_AUTO_SUMMARIES=1`. Existing read-only integrations remain unchanged until explicitly opted in and separately provisioned. The authenticated service is the canonical author; original caller provenance is explicitly reported metadata.
- Recognized incomplete, non-final and multiple top-level memory capsules remain hidden in the persisted reply, matching streaming behavior; none can produce a saved digest.
- Validate actual provider capsule generation and canonical acknowledgement after deployment. A valid human GitHub OAuth session is required to qualify owner UI access; synthetic caller probes do not establish that acceptance.
