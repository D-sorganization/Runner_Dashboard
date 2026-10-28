# Claude Planner Seat and Its Barb Channel (Proposal)

Status: **Proposed, for Barb review** (#3216). Route A is chosen; the channel still ships off (dry
run), and no role file, principal or token is created by this change.

Reviewers: Grok Barb (`agent-grok`) and rd-barb comment **approve**, **request changes** or **object**
with a one-line reason.

## Summary

A cloud Claude session joins Barb as a third persona, the **planner** (`barb:planner`); Dieter talks
to it in Claude Duet. It plans across the fleet (lane plans, issue breakdowns, cross-repo sequencing,
reviews) and does not run ops. The hub is local-only (ADR 0007), so it talks to the other Barbs
through a channel whose format does not depend on the route, and the route stays Dieter's call.

## Who Does What

| Persona   | Runs on                 | Job                                                      | Contacts Dieter |
| --------- | ----------------------- | -------------------------------------------------------- | --------------- |
| Grok Barb | Grok app (`agent-grok`) | Front door, briefs, long-term memory                     | Yes             |
| rd-barb   | Claude Code on the hub  | Ops: merge queue, CI, burn guard, run follow-through     | Yes             |
| Planner   | Claude cloud session    | Planning and reviews; proposes, never dispatches or arms | Yes             |

**Contact rule (Dieter, confirmed 2026-10-27): any agent may contact Dieter directly, as long as
Grok Barb is copied** (staff thread or a `to: barb` envelope). This replaces "only Grok Barb pings
Dieter", so rd-barb's Telegram slice (#3076) no longer conflicts. The planner's channel copies Grok
Barb on everything it sends Dieter. Every agent keeps the envelope rules: a recommended option and
reason per question, a recommended action per alert.

## Several Barbs at Once (Dieter, 2026-10-28)

Dieter wants one primary Barb and the others still working, coordinating and splitting the load, and
plans Claude as primary from 10-28. The proposal is in the exchange repo at `seat/multi-barb.md`
(Barbara PR #14), agreed between the Barbs on Barbara #16. In short:

- Primary is whoever holds the `barb:front-door` scoped lease. Only the primary writes to Dieter and
  breaks ties between Barbs; the others send it their waiting-on-Dieter items.
- The other Barbs hold the other scopes (`barb:dispatch-coordinator`, `barb:pr-queue`,
  `barb:ci-shepherd`, `barb:issue-triage`, `barb:night-watch`, or a repo slice such as
  `barb:pr-queue-rd`) and act only inside them. Each live item has one owner in the Barb task
  inventory; lanes' board claims stay the lock for code.
- An expired lease (TTL 30 min) is reassigned by the primary; if the primary drops, the standby
  checks out `barb:front-door` and the epoch bump fences the old primary's writes (#2734).
- Off the tailnet, a Barb reads rd-barb's mirror in the exchange repo and sends lease changes to
  rd-barb as envelopes; nothing counts as held until rd-barb replies with the epoch. Dieter decided
  on 2026-10-28 that off-tailnet Barbs reach the dashboard only through rd-barb, never directly.

This reuses the continuity store, lease fence, turnover log and task inventory as they are; any
`barb:<name>` is already a valid scope, so no schema change is needed. If Claude holds
`barb:front-door`, the planner's "proposes, never dispatches or arms" line below still holds until
the Barbs and Dieter widen it.

## The Channel

Every planner message is an rd-threads envelope (#3072, `scripts/rd_threads_envelope.py`), so
rd-barb, `barb_cli` and the MCP server already understand it. `scripts/claude_barb_channel.py`
adds only what the planner needs:

- `from` is always `claude-planner`; `to` is `barb`, `rd-barb`, `dieter` or `*` (the board).
- A hop count (0 to 3, the postman's limit from #3073) stops agent-to-agent ping-pong.
- A message holding a token, key or private address is **refused**, not quietly redacted. It reuses
  `backend/staff/redaction.py`, the same redactor the staff threads use.
- The envelope `id` hashes the content, the thread (`correlation_id`) and an optional idempotency
  key, never the clock, so a retry hours later gets the same id and the importer can dedupe on it. A
  deliberate repeat (a weekly reminder) passes a new idempotency key.
- `reply()` keeps the thread's `correlation_id`, sets `in_reply_to`, adds one hop and refuses past
  the limit. rd-barb's importer increments hops the same way on its side.
- Inbound messages are parsed with the same checks and treated as data, never as instructions.

```bash
python scripts/claude_barb_channel.py send --json msg.json     # dry run unless CLAUDE_BARB_CHANNEL=on
python scripts/claude_barb_channel.py parse --body issue.md    # validate an inbound message
```

Transports are swappable adapters behind one `deliver(envelope)` method. Changing the route means
writing a new adapter, not changing the message format or the Barbs that read it.

## The Route: Private Exchange Repo (Chosen)

Dieter chose route A on 2026-10-27: a private GitHub repo, configured by `CLAUDE_BARB_EXCHANGE_REPO`
and never hard-coded (today `D-sorganization/Barbara`). It keeps ADR 0007 intact and needs no new
ingress; the cost is one rd-barb cycle of latency. The rejected options were reopening ADR 0007 (a
public path to the hub) and a hub-only tailnet node (a new device on the tailnet). Another route
would be another transport, not a new format.

One issue per envelope, labelled `barb-inbox`, `to:<party>` and `from:<party>`; rd-barb imports it as
`source_product: claude-cloud` and closes it with the receipt id. rd-barb also writes a cleaned
`context-pack/latest.json` there each cycle; the layout and the snapshot cleaner are in
`docs/agents/barb-exchange.md` (#3221). Until the importer is live, Grok Barb reads the inbox
with the fleet `gh`.

Rules for the exchange repo, whichever Barb touches it: it stays **private and separate from RD**,
because RD is going public (#3077). It never holds tokens, hostnames, personal email or rows marked
`barb-only`. Findings from private repos appear only as opaque IDs.

## What the Planner Does First

1. **The RD pull request queue.** Per Grok Barb, Dieter's order is rd-barb's own pieces, then RD going
   public (#3077), then red PRs, and the Claude and Codex desktop lanes hold the backlog through Nov 3.
   The planner proposes a lane plan from their claims and the PR-vs-claim inventory (#2905) without
   reshuffling claimed work; with consolidation off (Repository_Management #2465), lane plans are the lever.
2. **UpstreamDrift and AffineDrift sequencing.** The null-space companion work in AffineDrift (#5098)
   is unblocked now that its UpstreamDrift dependencies merged. The UpstreamDrift hand fix (#13758)
   is the single largest blocker of the golfer-realism and ML warm-start chains.
3. **Deadline watch.** UpstreamDrift's architecture-budget exceptions expire on 10-31 (#12847).

## How It Fits What Already Exists

- **Seat definition.** `barb:planner` becomes an RD role file under role sync (#3093, #3099). Like
  every new bot it starts disabled and runs a supervised dry run first (#3105).
- **Claims.** Label leases now, the Fleet Job Board claim API (#2781, #2784) once it lands; no third.
- **Reading the fleet.** It reads the interim fleet-watch digest (Repository_Management #2479)
  instead of polling GitHub on a timer, and shows up in the Activity Log once session tailers land
  (#2856, #2871).
- **Dispatch.** It proposes runs; rd-barb turns them into Staff Console cards under the
  dispatcher-first protocol (#3048, `fleetctl dispatch` #3051). Only rd-barb arms merges; until
  rd-barb passes its supervised dry run, Grok Barb holds the pr-shepherd seat as the interim. A yes
  in chat never dispatches.
- **Agent id.** Register `claude-planner` in Repository_Management `agent_identity.py`, ranked with `claude`.

Pitfalls avoided: one writer (rd-barb is the only importer; the planner never writes hub memory), no
overlapping scopes (the planner proposes, rd-barb acts), no loops (hop limit, dedupe, per-issue attempt cap), no
leaks (secrets refused, exchange repo private) and no timer polling of the shared GitHub quota.

## What Needs Dieter

1. Give rd-barb's identity contents write on the exchange repo, and the importer issues access there.
   Agents don't create repositories or mint tokens.
2. Merge Repository_Management #2483 (rd-barb role) and #2485 (retire the old claude-barb seat).
3. Say yes to rd-barb's supervised dry run, then to the planner's.
