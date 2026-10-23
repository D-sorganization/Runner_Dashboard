---
issue: 2203
summary: "CI dispatch falls back from missing or cooling providers, preserves requested quality tier across provider models, and records the admitted provider and model"
dl_state: "in_review"
next_step: "Pass protected checks and publish a qualified runtime release; retain provider auth work with its existing owner"
branch: "bot/issue-2203-ci-provider-fallback"
---

The shared selector checks installed executables and follows configured default
chain order within role permissions. Explicit unknown, disabled, disallowed and
chat-only providers still refuse. Fallback resolves a model for its provider,
validates the original explicit primary model, and preserves internal CI quality
intent. Known lower-tier configured fallback models are raised to the required
tier. CI audit records the admitted provider/model and retains its single-run
lock and all-providers-unavailable refusal.

Peer targeting occurs before local provider planning, so the destination applies
availability and auth checks. Internal CI model tiers are local-only until peers
support that trusted field; forwarding must refuse rather than silently discard
a nonempty tier. Existing Claude login/preflight work remains with its owner.
