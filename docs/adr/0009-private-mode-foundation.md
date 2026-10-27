# ADR 0009: Private Mode foundation (PM-0 / #3250)

## Status

Accepted (2026-10-27). Implements the Board special session on epic #3249; Dieter
decisions recorded below supersede open items in the epic where noted.

## Context

Runner Dashboard must support repositories and threads classified as sensitive
enterprise work without leaking content to shared fleet surfaces (hub logs,
transcripts, metrics, GitHub CI artifacts). The epic (#3249) adds an opt-in
**Private Mode** behind an overlay flag so normal operation stays unchanged when
the flag is off. PM-0 establishes the decision record, threat model, overlay
schema field, three-level classification enum, INTERNAL semantics, and the
safe-disable contract before dispatch, emit, and private CI slices land.

## Decisions

### Classification levels (three, total order)

Keep **PUBLIC**, **INTERNAL**, and **SENSITIVE** with max-join on the greater
level. Names are uppercase strings in overlay policy (parsed by
`backend/private_mode.py`).

| Level     | Meaning (summary) |
| --------- | ----------------- |
| PUBLIC    | Default fleet operation; existing redaction and shared sinks. |
| INTERNAL  | Pattern redaction applies; **no metadata-only projection** to shared sinks; approved cloud providers may be used when `private_mode` is enabled. |
| SENSITIVE | Strongest lane: shared sinks receive typed metadata projections only (later slices); provider admission is restricted (below). |

When `private_mode` is absent or `enabled` is false, effective classification
defaults to **PUBLIC** everywhere (safe-disable).

### SENSITIVE provider destinations

For **SENSITIVE** work with Private Mode enabled, approved destinations are
**Claude** and **Codex** (subscription-local / fleet-app execution). **Cloud
agents** (Cursor Cloud and other hosted agent sessions) are **not** admitted at
this level. Per-repo and per-dispatch allowlists live in overlay policy
(`policy_dir`); PM-1+ enforce admission.

### Feature flags

- **`private_mode.enabled`** (overlay): master switch, **off by default**. No
  behaviour change in core paths when off (tested).
- **`private_mode.metric_route_templating.enabled`**: separate flag, **off by
  default**. When off, metric route templating behaves as today. Fleet-wide
  enablement is recommended in runbooks but not automatic.

### Private CI pool

Private CI/CD for SENSITIVE repos uses an **RD-owned controller** and a **small
Desk sandbox pool** (ephemeral sandboxes, no host mounts or long-lived creds).
GitHub-hosted runners are not used for SENSITIVE repos. Pool provisioning requires
operator approval before Wave C (#3265–#3268).

### PM-18 (node identity / signed envelopes)

**Deferred** to Wave D (#3269). Documented here so PM-10+ do not assume signed
cross-node envelopes in Wave A/B.

### Composes with existing overlay (#3079)

The `private_mode` block is optional JSON on the version-1 overlay. It is read
only through `get_overlay()` / `read_private_mode()` (LoD). Policy, repo maps,
keys, and evidence remain under `policy_dir` and sibling private repos—not in the
public core.

## Consequences

- **Easier:** Later slices share one `Level` type, one flag, and one threat
  model; publish guard stays green with placeholder fixtures only.
- **Harder:** Operators must maintain overlay policy for levels and providers;
  enabling `private_mode` without Wave B/C slices does not yet deliver full
  isolation—only the contract and flag wiring exist in PM-0.
- **Follow-ups:** PM-1 resolver, PM-4+ `emit_shared`, private CI PM-15–17,
  PM-18 when scheduled, supervised dry run PM-21 before any production enable.

## References

- Epic #3249, slice #3250
- [Private Mode threat model](../security/private-mode-threat-model.md)
- [Overlay contract](../architecture/overlay-contract.md)
