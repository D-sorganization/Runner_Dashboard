# #3825 — Unreachable barb-worker Hyper-V VMs

## Problem

When barb-worker guests are down, hub livez/Tailscale/bootstrap checks fail without a single operator path.

## Acceptance

1. **Runbook** — Detection matrix (hub livez, Tailscale, bootstrap verify) and remediation (Hyper-V power state, networking, guest tools) with example node names only.
2. **Cross-link** — `docs/agents/barb-worker-vm.md` and fleet topology examples.
3. **Doc-only** acceptable via #3829.

## Open PR

- [#3829](https://github.com/D-sorganization/Runner_Dashboard/pull/3829)
