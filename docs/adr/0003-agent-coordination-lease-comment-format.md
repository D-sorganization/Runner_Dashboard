# 0003. Plain-text lease comments for agent coordination

## Status

Accepted — protocol described in `CLAUDE.md`, `CONTRIBUTING.md`, and (in `Repository_Management`) `docs/agent-lease-protocol.md`. Enforced by `.github/workflows/Agent-Lease-Reaper.yml` and `scripts/agent_lease_reaper.py`. The matching `claim:<agent>` label set is owned by `Repository_Management`.

## Context

The fleet runs multiple AI agents in parallel (Claude, Codex, Jules, GAAI, Maxwell, plus humans). All of them watch the same issue tracker, and all of them are capable of opening PRs against this repository. Without coordination, two agents routinely picked up the same issue, wasted compute, and produced duplicate or conflicting PRs that the maintainer had to triage by hand.

Several heavier coordination mechanisms were considered:

- A dedicated coordination service (Redis / Postgres / etcd) keyed by issue number.
- A YAML or JSON file checked into the repo recording active claims.
- A custom GitHub App with a database backend.
- Pure label-based locking (`claim:claude`) without any metadata.

Each had drawbacks for this fleet:

- A side-channel database adds infrastructure that every agent must reach, and `runner-dashboard` is supposed to be runnable from a laptop without provisioning anything else.
- A repo-checked-in claim file forces every claim and release to be a commit + push, racing other agents' commits and triggering CI on every lease change.
- A GitHub App needs an installation, secrets, and a maintainer with admin rights — too heavyweight for a coordination primitive.
- Pure labels lose the timing information; the reaper cannot tell a healthy long-running agent from a crashed one.

The project also wants the protocol to be _legible_ — a human reading an issue should be able to see at a glance who is working on it and when their claim expires, without going to a separate dashboard.

## Decision

Use **plain-text "lease" comments on the issue itself**, in a strict single-line format, as the source of truth for active claims. Wrapped by a `claim:<agent>` label for fast filtering and by the Agent Lease Reaper workflow for cleanup.

The required comment format is:

```
lease: <agent-id> expires <ISO-8601 timestamp>
```

Example: `lease: claude-398 expires 2026-04-30T18:00:00Z`

Rules:

1. Before starting work on an issue an agent **must** post a lease comment with an expiry no more than two hours in the future.
2. The agent renews the lease (posts a new lease comment) every two hours while work is ongoing.
3. The most-recent lease comment wins; older comments are advisory only.
4. The lease is released by either letting it expire, opening a PR that references the issue, or posting a "release" / explicit unclaim comment.
5. When two agents race on the same issue, the priority order in `CLAUDE.md` resolves: `user > maxwell-daemon > claude > codex > jules > local > gaai`.
6. The `Agent-Lease-Reaper.yml` workflow runs every 30 minutes, parses the latest lease comment, and removes the matching `claim:<agent>` label if the lease is expired and there is no open PR referencing the issue.

The format is deliberately _not_ YAML, JSON, or front-matter. It is a single self-describing line that:

- A human can read at a glance.
- A regex (`^lease:\s+(\S+)\s+expires\s+(\S+)`) can parse without a YAML library.
- Cannot be ambiguous about which field is the agent and which is the timestamp.
- Survives Markdown renderers, code-block wrapping, and copy-paste from chat.

The richer "design opinion" structure used for `panel-review` issues _is_ a structured (YAML-style) block, defined in `docs/issue-taxonomy.md`. That is intentional — opinions need fields like `verdict`, `risks`, `alternatives`. Leases need only an agent identifier and an expiry, so the format stays minimal.

## Consequences

**Easier:**

- Zero infrastructure: leases are GitHub issue comments. Any agent that can already post comments can participate.
- Audit trail is automatic — every claim and renewal is a comment with author, timestamp, and edit history.
- The reaper is a small, idempotent workflow that uses only the GitHub API.
- Humans reading an issue see the active claim immediately.
- The protocol is portable: `Repository_Management` and `Maxwell-Daemon` can adopt the identical format with no schema negotiation.

**Harder / cost:**

- Comment-based coordination is eventually consistent — there is a small window in which two agents can both believe they hold the lease before either sees the other's comment. The priority order resolves these races deterministically but does not prevent duplicated work in the race window.
- Renewal discipline is on the agent. A crashed agent leaves a label up for at most one reaper cycle (~30 minutes) plus the lease's remaining TTL.
- Format drift is a real risk — a typoed `lease:` line silently fails to register. Mitigation: agents should post via a shared helper (in `Repository_Management/shared_scripts/`) rather than hand-formatted strings, and the reaper logs unparseable lines.
- The lease comment is just a comment — it cannot block a PR from being opened by a different agent. Enforcement is post-hoc via the Redundant PR Closer workflow, not pre-emptive.

If contention grows past what the priority order can resolve, the contract is structured to allow lifting leases into a stronger backend (a coordination service, a single `coordination` repo) without changing the human-visible format.

## Addendum (2026-11-02, #3300 upgrade 2): the `bar:` field — freezing the acceptance bar at claim time

Status: accepted. Expert Panel verdict 4-0, two rounds (thread `th_b01730d15a37`), recorded on
Runner_Dashboard#3300: "Bar frozen at claim: adopt. Hash the canonicalized checklist, not the raw
text. A mismatch forces a re-claim." Second of the four ADR/spec docs in #3300's build order
(ADR 0010 is the first); the implementation PR for this upgrade follows after this doc merges.

**Problem.** A claim fixes *who* is working an issue (the existing lease format above), but not
*what they were asked to do*. An issue's acceptance criteria can change — a checkbox added, a
Dependencies section edited — after the lease is taken, while the builder keeps working from the
version they first read. The verifier (ADR 0010) then judges the PR against whatever criteria are
on the issue *now*, which may not be what the builder claimed against.

**Decision.** Extend the lease comment with one additional field, appended to the existing line:

```
lease: <agent-id> expires <ISO-8601 timestamp> bar:<criteria-hash>
```

Example: `lease: claude-398 expires 2026-04-30T18:00:00Z bar:3f9a1c2e...`

- `<criteria-hash>` is a hash of the issue body's **canonicalized** acceptance-criteria checklist at
  claim time — not the raw markdown text. Canonicalization strips checkbox check-state, whitespace,
  and comment/thread content outside the checklist, so an unrelated edit elsewhere in the issue body
  (a typo fix, a new comment) does not change the hash; an actual criteria edit does.
- The hash is computed and stored once, when `post_agent_lease` writes the claim; it is never
  recomputed for the life of that lease.
- A builder's PR is judged (by the ADR 0010 verifier) against the criteria captured in `bar:` at
  claim time, not against the issue's current body.
- If the issue's canonicalized criteria no longer match the lease's stored `bar:` hash — the
  criteria changed after the claim was taken — the lease is stale: `check_agent_claim` reports a
  bar mismatch and the holder must re-claim (posting a fresh lease with the new hash) before the
  verifier will accept a PR against the new criteria. A stale-bar PR judged against old criteria is
  not silently accepted as meeting the new ones.
- The existing lease format (agent id, expiry, priority order, renewal, release, reaper) is
  unchanged; `bar:` is additive and parsed by the same single-line regex extended with one more
  capture group, so a lease comment with no `bar:` field (older agents, or issues with no
  checklist) stays valid exactly as it is read today.

**Lands in (implementation PR, not this doc):** `backend/coordination/claims.py` (canonicalize +
hash the checklist at claim time; compare on `check()`), and the shared RM lease-writer helper
(`post_agent_lease`) that already owns the single-line format this ADR defines.

**Consequences.**

- *Easier:* the verifier always judges a PR against a frozen, named version of the bar; a Barb or
  reviewer disputing a PR can point at the exact `bar:` hash it was built against.
- *Harder / cost:* an issue whose criteria legitimately need mid-flight clarification now requires
  an explicit re-claim rather than a silent continuation; this is intentional (the panel's "a
  mismatch forces a re-claim"), but it does add one more step to a long-running claim when the
  issue author tightens wording after work has started.
- Canonicalization itself needs a precise, tested definition (what counts as "the checklist", how
  whitespace/ordering is normalized) — that is implementation-PR scope, not this ADR.
