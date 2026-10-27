# Private Mode — high-level threat model

Epic #3249, PM-0 (#3250). This document states **categories** of risk and
control intent only. It does **not** list vulnerability specifics, CVEs, or
customer data. Related hardening continues under #2566.

## Assets

- Sensitive repository content, prompts, and agent transcripts
- Private CI logs and build artifacts
- Credentials, classification policy, and overlay configuration
- Metadata-only audit trail (hash-chained in later slices)

## Actors

- Outside observer of shared fleet surfaces (hub UI, metrics, alerts, GitHub checks)
- Compromised or misbehaving agent or worker
- Hostile code executed inside CI sandboxes
- Compromised peer node in the fleet
- Operator misconfiguration or mistake

## Threat categories (non-specific)

| Category | Control direction |
| -------- | ----------------- |
| Content or metadata leaking to shared sinks | Typed projections per level; `emit_shared()` choke point (PM-4+); pattern redaction for INTERNAL+ |
| Level loss during propagation (retries, sub-dispatch, forks) | Immutable envelope with max-join level (PM-2+); downgrade requires audited declassify |
| Provider egress of sensitive prompts | Admission checks actual provider destination; SENSITIVE limited to approved Claude/Codex paths |
| CI code reaching credentials or forging results | Ephemeral Desk sandbox pool; pinned gate manifest; status publisher exposes state + opaque run id only |
| Cross-node spoofing | Deferred PM-18 (signed envelopes / node identity) |
| Audit tampering or truncation | Single audit writer, hash chain + checkpoints (PM-9) |
| Deletion residue (backups, WAL, indexes) | ADR documents residue; crypto-shred cannot resurrect shredded threads (PM-8) |
| Self-approval of sensitive dispatch | Separation of duties + metadata-only audit events |
| Stalls and quota burn | Explicit PARKED_* states with owner and deadline; SENSITIVE lanes do not block PUBLIC/INTERNAL |

## Safe-disable assumption

With `private_mode.enabled` false or absent, the system behaves as today: default
**PUBLIC**, no new admission or emit paths, and no private CI scheduling. Tests
lock this contract.

## Placeholders

Fixtures, issues, and CI use synthetic names only (e.g. **Example Corp**). Real
customer or employer data stays out of the public core.
