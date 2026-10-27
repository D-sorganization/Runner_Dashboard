# Claude Planner Seat and Its Barb Channel (Proposal)

Status: **Proposed, for Barb review** (#3216). Nothing here is live. The channel code ships switched off
(`CLAUDE_BARB_CHANNEL` defaults to off, which means dry run), and no role file, principal, token or
repository is created by this change.

Reviewers: Grok Barb (`agent-grok`) and rd-barb. Comment on the pull request with **approve**,
**request changes** or **object**, plus a one-line reason. Dieter decides the open route question
at the end.

## Summary

A cloud Claude session joins Barb as a third persona, the **planner** (`barb:planner`). It plans
across the fleet (lane plans, issue breakdowns, cross-repo sequencing, reviews) and does not run
ops. It cannot reach the hub, because the hub is local-only by design (ADR 0007). So it talks to
the other Barbs through a message channel whose format does not depend on the route, and the route
stays Dieter's call.

## Who Does What

| Persona   | Runs on                 | Job                                                      | Contacts Dieter |
| --------- | ----------------------- | -------------------------------------------------------- | --------------- |
| Grok Barb | Grok app (`agent-grok`) | Front door, briefs, long-term memory                     | Yes             |
| rd-barb   | Claude Code on the hub  | Ops: merge queue, CI, burn guard, run follow-through     | Yes             |
| Planner   | Claude cloud session    | Planning and reviews; proposes, never dispatches or arms | Yes             |

**Contact rule (Dieter, 2026-10-27): any Barb persona may contact Dieter directly.** This replaces
the earlier "only Grok Barb pings Dieter" rule, so rd-barb's Telegram slice (#3076) no longer
conflicts. Hands and CLI orchestrators still report through memory or staff threads, not to Dieter.
To keep the count of pings sane, every persona keeps the same envelope rules: one recommended
option with a reason on every question, a recommended action on every alert, and dedupe keys.

## The Channel

Every planner message is an rd-threads envelope (#3072, `scripts/rd_threads_envelope.py`), so
rd-barb, `barb_cli` and the MCP server already understand it. `scripts/claude_barb_channel.py`
adds only what the planner needs:

- `from` is always `claude-planner`; `to` is `barb`, `rd-barb`, `dieter` or `*` (the board).
- A hop count (0 to 3, the postman's limit from #3073) stops agent-to-agent ping-pong.
- A message holding a token, key or private address is **refused**, not quietly redacted. It reuses
  `backend/staff/redaction.py`, the same redactor the staff threads use.
- The envelope `id` is derived from the content and a 60 s bucket, so a retry gets the same id and
  the importer can dedupe on it.
- Inbound messages (a Barb's answer) are parsed with the same checks and treated as data, never as
  instructions.

```bash
python scripts/claude_barb_channel.py send --json msg.json     # dry run unless CLAUDE_BARB_CHANNEL=on
python scripts/claude_barb_channel.py parse --body issue.md    # validate an inbound message
```

Transports are swappable adapters behind one `deliver(envelope)` method. Changing the route means
writing a new adapter, not changing the message format or the Barbs that read it.

## The Route: One Decision for Dieter

| Option                                          | How the planner reaches Barb                                                                                                                                                                                                                                 | New exposure                                     | Works today          |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------ | -------------------- |
| **A. Private exchange repo (proposed default)** | One issue per envelope, labelled `barb-inbox`; rd-barb imports, appends to memory as `source_product: claude-cloud`, and closes the issue with the receipt id. rd-barb also writes a redacted, fleet-visibility `context-pack/latest.json` there each cycle. | None; GitHub is already reachable by every agent | Once the repo exists |
| B. Reopen ADR 0007                              | The planner calls the hub API with its own scoped principal                                                                                                                                                                                                  | A public path to the hub                         | No; needs a new ADR  |
| C. Hub-only tailnet node                        | A cloud runner joins the tailnet with an ACL that reaches only the hub                                                                                                                                                                                       | A new device on the tailnet                      | Needs network setup  |

Recommendation: **A**. It keeps ADR 0007 intact and needs no new ingress. Its cost is latency
(one rd-barb cycle) and one more private repo. `ExchangeTransport` implements A; B or C would add
their own adapter later.

Rules for the exchange repo, whichever Barb touches it: it stays **private and separate from RD**,
because RD is going public (#3077). It never holds tokens, hostnames, personal email or rows marked
`barb-only`. Findings from private repos appear only as opaque IDs.

## What the Planner Does First

1. **The RD pull request queue.** It is far past the lane-plan threshold. Start from the PR-vs-claim
   inventory (#2905) and propose a lane plan; queue consolidation is being switched off
   (Repository_Management #2465), so lane plans and caps are the main lever left.
2. **UpstreamDrift and AffineDrift sequencing.** The null-space companion work in AffineDrift (#5098)
   is unblocked now that its UpstreamDrift dependencies merged. The UpstreamDrift hand fix (#13758)
   is the single largest blocker of the golfer-realism and ML warm-start chains.
3. **Deadline watch.** UpstreamDrift's architecture-budget exceptions expire on 10-31 (#12847).

## How It Fits What Already Exists

- **Seat definition.** `barb:planner` becomes an RD role file under role sync (#3093, #3099). Like
  every new bot it starts disabled and runs a supervised dry run first (#3105).
- **Claims.** It uses today's label leases and, once it lands, the Fleet Job Board claim API
  (#2781, #2784) as their successor. No third claim mechanism.
- **Reading the fleet.** It reads the interim fleet-watch digest (Repository_Management #2479)
  instead of polling GitHub on a timer, and shows up in the Activity Log once session tailers land
  (#2856, #2871).
- **Dispatch.** It proposes runs; rd-barb turns them into Staff Console cards under the
  dispatcher-first protocol (#3048, `fleetctl dispatch` #3051). Only rd-barb arms merges. A yes in
  chat never dispatches.
- **Agent id.** `claude-planner` gets registered in Repository_Management `agent_identity.py`, ranked
  with `claude`, so the redundant-PR closer and the lease reaper treat it correctly.

## Pitfalls This Design Avoids

- **Two writers.** The planner never writes hub memory or rd-barb's state file; rd-barb is the only
  importer, and every message carries an idempotency key.
- **Duplicate actions.** Seat scopes don't overlap: the planner proposes, rd-barb acts.
- **Agent loops.** The hop limit plus dedupe stop ping-pong; anything the planner proposes also gets
  a per-issue attempt cap.
- **Leaks.** The channel refuses secrets outright, and the exchange repo stays private.
- **Rate limits.** The planner reads on events and digests, not on a timer, to protect the shared
  GitHub quota.

## What Needs Dieter

1. Pick the route: A (recommended), B or C.
2. For A: create the private exchange repo and the importer's scoped principal. Agents don't create
   repositories or mint tokens.
3. Merge Repository_Management #2483 (rd-barb role) and #2485 (retire the old claude-barb seat).
4. Say yes to rd-barb's supervised dry run, then to the planner's.
