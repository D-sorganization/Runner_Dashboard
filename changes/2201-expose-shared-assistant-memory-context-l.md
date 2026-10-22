---
issue: 2201
summary: "Expose shared assistant memory context, listing and append through the provider-neutral Fleet CLI and MCP client with validated single-attempt writes"
dl_state: "in_review"
next_step: "Pass protected checks and connect the local assistant to the existing Barb context authority with its scoped identity"
branch: "bot/issue-2199-runtime-integration"
---

Adds access to existing scoped memory APIs. It does not copy memory databases or promote a Barb authority. The ControlTower assistant connection and cross-interface acceptance remain tracked in #2199.
