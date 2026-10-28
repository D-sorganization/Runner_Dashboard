# Role Seats: Barb's Grok Bot Roles in Runner Dashboard (Proposal)

Status: **Proposed, for Barb review. Do not merge.** Nothing here is built or switched on. Dieter asked on
2026-10-27 to mirror every role Barb runs in the Grok Bot app as its own seat, mostly in Runner Dashboard
(RD), each with a CLI agent that does the role and talks to other agents, and with a fallback to the Grok
Bot app or Claude Duet when the CLI agent or RD is unavailable.

Reviewers: Grok Barb, rd-barb and the Claude planner comment **approve**, **request changes** or
**object** with a one-line reason. Companion proposals: the planner seat and its Barb channel
([claude-planner.md](claude-planner.md), PR #3217) and persistent messengers (`messenger-agents.md`,
PR #3231). This is a separate PR because it covers every role, not only messaging; it reuses #3231's
postman and doorbells rather than adding a transport.

## Summary

Most of the Grok Bot roster already left the Grok app on 2026-09-23 (Repository_Management
`docs/grok-bridge.md`): each became a staff role file in Repository_Management `staff/roles/`, run by the
RD scheduler, with its Simpsons-themed picture in `staff/avatars/`. What is missing is the **seat**: one
named place per role that has a CLI agent assigned to it, an address other agents can message, a memory
home and a picture, and that stays reachable when its CLI agent or RD is down.

A seat is not a new bot. It is the existing role file plus four bindings:

| Binding   | Where it lives                                                                                                                      |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Role      | Repository_Management `staff/roles/<role>.yml` (public), or the RD private overlay `private_roles` (#3094)                          |
| CLI agent | The role's first `providers` entry, run headless by RD (`claude -p`, `codex exec`, `agy -p`); failover below                        |
| Address   | The role name as a roster agent on the coordination board and agent bus; `to:<role>` in the Barbara inbox                           |
| Memory    | Public: `staff/knowledge/<role>.yml` knowledge pack. Operator-local: a capped living-guidelines doc. Private: overlay context store |
| Picture   | `staff/avatars/<role>.webp`, or `staff/avatars/personas/<slug>.webp` for a persona with no role file                                |

## The Roster

Sources: Repository_Management `staff/roles/*.yml` (26 files), `staff/avatars/` (17 role pictures and 12
persona pictures), `docs/org-chart.md` (the Grok Chat Bots table) and `docs/grok-bridge.md`. Grok Barb is
asked to confirm the live Grok Bot list and send any missing pictures and memory in the private exchange
repo (Barbara issue #15). This table is a draft until she answers.

### 1. Fleet Roles That Already Have a Role File

Seat runtime is the first provider in the role file. "Grok" marks roles that also had a Grok Bot.

| Seat                 | Picture | Grok | Seat runtime | Reports to  | Chat | Duet channel |
| -------------------- | ------- | ---- | ------------ | ----------- | ---- | ------------ |
| barb                 | yes     | yes  | claude       | user        | yes  | yes (exists) |
| night-watch          | yes     | yes  | claude       | barb        | yes  | no           |
| issue-remediator     | yes     | yes  | claude       | night-watch | yes  | no           |
| pr-remediator        | yes     | yes  | claude       | night-watch | yes  | no           |
| sanitation           | yes     | yes  | claude       | night-watch | yes  | no           |
| cartographer         | yes     | yes  | claude       | barb        | yes  | no           |
| test-hygiene         | no      | no   | claude       | night-watch | yes  | no           |
| board-secretary      | yes     | yes  | claude       | barb        | yes  | no           |
| fleet-critic         | yes     | yes  | claude       | barb        | yes  | no           |
| pragmatic-programmer | yes     | yes  | claude       | barb        | yes  | no           |
| research-scout       | yes     | yes  | claude       | barb        | yes  | no           |
| oss-scout            | yes     | yes  | claude       | barb        | yes  | no           |
| librarian            | yes     | yes  | claude       | barb        | yes  | no           |
| mad-scientist        | yes     | yes  | claude       | user        | yes  | no           |
| vision-quest         | yes     | yes  | claude       | user        | yes  | no           |
| disciple             | yes     | yes  | claude       | user        | yes  | no           |
| project-steward      | no      | no   | claude       | barb        | yes  | no           |
| product-owner        | no      | no   | claude       | barb        | no   | no           |
| chief-architect      | no      | no   | claude       | barb        | no   | no           |
| code-reviewer        | no      | no   | claude       | barb        | no   | no           |
| fleet-curator        | no      | no   | claude       | barb        | yes  | no           |
| maintenance          | no      | no   | claude       | barb        | yes  | no           |
| tooling-scout        | no      | no   | claude       | barb        | yes  | no           |
| bean-counter         | no      | no   | codex        | barb        | yes  | no           |

Retired and not seated: `orchestrator` (folded into barb) and `usage-tracker` (replaced by
`GET /api/staff/usage`). rd-barb's role file is in Repository_Management #2483 and becomes a seat when
that merges. The planner seat (`claude-planner`) is #3217.

Nine roles were born in RD and never had a Grok Bot picture. Picking Simpsons-themed pictures for them is
Dieter's call; until then the Staff Console keeps its default.

### 2. Grok Bot Personas With No Role File

Pictures for all of these are already in `staff/avatars/personas/`.

| Persona                          | Job (org chart)                                           | Proposed seat                                                                                                       |
| -------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| PM-UpstreamDrift, PM-AffineDrift | Project manager per priority-1 repo                       | New thin role files `pm-upstreamdrift`, `pm-affinedrift` (project-steward duties plus a persona). Duet channel each |
| PM-Gasification, PM-Sidekick     | Project manager per repo                                  | Same pattern, `pm-gasification`, `pm-sidekick`; Sidekick packaging hold stays. No Duet channel                      |
| Karl, Feynman                    | AffineDrift writing gate (lay reader, prose)              | New chat-only role files, no push or lease; `pm-affinedrift` defers to them                                         |
| Linus                            | Open-source contributions                                 | Stays in Grok Bot for now: upstream contributions reach people outside the fleet, so Dieter decides scope first     |
| Professor Frink                  | Prototypes                                                | Probably `mad-scientist`; Grok Barb to confirm                                                                      |
| Fleet-Librarian                  | Drive library curation                                    | `librarian` (its role file already has the Drive connector)                                                         |
| Fleet-Dispatcher, Fleet Status   | Dispatch, status                                          | Not seated: replaced by `POST /api/staff/{role}/run` and `GET /api/staff/summary`                                   |
| Three personal personas          | Personal life, non-repo work tasks, productivity watchdog | Private seats only (next section); never in this public repo or in any shared memory                                |

### 3. Personal Seats

Three personas handle Dieter's personal life and work outside the repo lanes (Repository_Management
`docs/org-chart.md` names them). RD is going public (#3077), so their role text, memory and mail never
live here or in the exchange repo. Two options, both Dieter's call:

1. **Private RD seat**: a `private_roles` entry in the private overlay (#3094), memory in the
   encrypted host-local context store, reachable only by its own role id. The non-repo work-task persona
   already has an RD staff thread that Barb hands tasks into, so it is the natural first private seat.
2. **Stay in Grok Bot**, with a Duet channel as the fallback. The personal-life persona is the only bot
   allowed Dieter's mail today, and Claude Duet already reads his mail, so a Duet fallback needs no new
   access.

## How a Seat Talks (Reuses PR #3231)

- **One address per seat.** Every seated role is a roster agent. The postman (#3231) delivers board
  messages, direct bus messages and, once slice 3 lands, Barbara `barb-inbox` issues labelled
  `to:<role>` into that seat's bus inbox, once each, deduped on the envelope `id`.
- **Doorbells, not polling.** The seat's CLI agent learns of mail through its runtime's hook at session
  start, or a budget-gated resume wake (#3231 slice 4). The wake prompt says only "you have N messages";
  the body is read from the inbox and treated as data.
- **Envelopes everywhere.** rd-threads envelopes (#3072), hop limit 3, secrets refused, a recommended
  option on every question. `claude_barb_channel.py` (#3217) widens its recipient list from the Barb
  personas to the seat roster read from the role files.
- **Contacting Dieter.** Any seat may, as long as Grok Barb is copied (Dieter, 2026-10-27): `to:dieter`
  plus `to:barb`. Barb stays the only seat that pings him unsolicited on a schedule.
- **Handoffs.** A chat reply ending `handoff: <role>` already moves work between roles in the Staff
  Console (`backend/staff/chat_handoff.py`); seats keep that path.

## Fallback: When the CLI Agent or RD Is Down

Three tiers. Each tier only answers, plans and records; none merges, arms auto-merge, dispatches runs,
writes holds or directives, or contacts anyone outside the fleet.

### Tier 1: The Seat's CLI Agent Is Unavailable (RD Is Up)

**Detect:** the run or chat turn fails with `auth_expired`, a quota or capacity error, a provider in
cooldown, or the role's liveness goes `late` or `dead` (`backend/staff/liveness.py`).

**Fall back:** RD already does this. `provider_fallback.py`, `failover_dispatch.py` and
`availability.py` move the run to the next provider in the role's `providers` list, on any machine that
has it, and chat turns get a fast acknowledgement and a degraded answer. The only change is to make
every seat's `providers` list end in a runtime that does not share the first one's account, so one
expired sign-in cannot take a seat dark. A seat whose last provider is `grok-chat` falls back to the Grok
Bot app through the existing Grok bridge.

### Tier 2: RD Is Unreachable

**Detect, inside the tailnet** (CLI agents on fleet nodes, Grok Barb through her local tool): the fleet
client's `GET /api/auth/me` fails three times in five minutes, or the hub reports ADR 0008 degraded mode.

**Detect, outside the tailnet** (Claude Duet can never reach the hub, ADR 0007): rd-barb adds a small
heartbeat to the exchange repo each cycle, `context-pack/heartbeat.json` with `generated_at` and
`hub_ok`. Two missed cycles mean RD is down. This reuses the snapshot writer, so it adds no new ingress
and holds no hostnames.

**Fall back:**

| Seat group                                                                                  | Falls back to                                             |
| ------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| barb (front door, briefs, routing)                                                          | Grok Barb in the Grok Bot app                             |
| Planner, PMs, steward, architect, product owner                                             | The Claude planner in Claude Duet                         |
| Chat seats Dieter talks to (critic, scouts, disciple, vision-quest, writing gate)           | Their Grok Bot persona if it still exists, otherwise Duet |
| Scheduled execution seats (night-watch, remediators, sanitation, maintenance, test-hygiene) | Paused; nothing runs until RD is back                     |
| Personal seats                                                                              | Grok Bot, then Duet                                       |

Messages to a seat go to the Barbara inbox as `to:<role>` envelopes while RD is down, so nothing is
lost and nothing needs RD to queue it.

### Tier 3: Everything Automated Is Down

Grok Barb or the Duet planner tells Dieter once, with what is paused and what waits on him. Nothing
else happens until a tier comes back.

### Staying in Sync After an Outage

- **One writer.** RD stays the authority (ADR 0008). A fallback agent never writes hub memory. It
  records each decision as a Barbara envelope (`kind: update`, the original `correlation_id`, a
  `fallback: true` note), so the outage leaves an ordered log.
- **Replay.** When the heartbeat is fresh for two cycles, rd-barb imports every envelope since the
  outage began (dedupe on `id`, ordered by creation time), and the postman delivers each to its seat.
- **Hand back.** Grok Barb and the planner each send one `kind: handoff` envelope listing what they
  answered and what is still open; the seat picks those up from its inbox.
- **Conflicts.** If something decided during the outage disagrees with RD's state (a claim, a status,
  a plan), the seat raises a `question` to Dieter with both versions. Neither side overwrites the other.
- **Memory.** Notes made during fallback are appended as `source_product: grok` or `claude-cloud`
  rows, never merged into existing rows by hand.

## Memory Migration From the Grok Bot App

1. Grok Barb commits each fleet role's memory to Barbara `roles/<slug>/memory.md`, cleaned like the
   context pack (no tokens, keys, hostnames, IP addresses, personal email or `barb-only` rows).
2. The Claude planner reviews each file and splits it: durable public knowledge goes to
   `staff/knowledge/<role>.yml` in Repository_Management by PR; operator-local guidance goes to the
   seat's living-guidelines doc (the 8 KB, lint-checked pattern of `rd_barb_memory.py`); anything
   personal or secret is flagged to Dieter and not copied.
3. Personal personas send purpose and rules only, never memory.

## Rollout (Each Slice Its Own PR, Each Off by Default)

1. This proposal, reviewed by the Barbs.
2. Repository_Management: role files for the PM and writing-gate personas, with avatar bindings.
3. RD: a seat registry built from the role files (role, runtime, address, memory, picture),
   read-only at `GET /api/staff/seats` and shown in the Staff Console.
4. Messaging: #3231 slices 2 to 4 with the seat roster as recipients.
5. Heartbeat in the exchange repo, then the Tier 2 fallback routing behind its own switch.
6. One supervised dry run per seat, then go-live per seat on Dieter's yes.

## Questions for Grok Barb (Barbara #15)

1. Which Grok Bot bots are live today, and is Professor Frink the `mad-scientist` role?
2. Which roles keep a Grok Bot persona as their Tier 2 fallback, and which can be retired from the app?
3. Any memory or pictures not already in Repository_Management.

## What Needs Dieter

1. Approve or change the roster and the fallback groups above.
2. Personal seats: private RD seat, or stay in Grok Bot with a Duet fallback.
3. Linus: seat it now, or keep it in Grok Bot until its upstream scope is set.
4. Which roles also get a Duet channel (proposed: PM-UpstreamDrift and PM-AffineDrift; barb exists).
5. Pictures for the nine RD-born roles.
6. Later, one yes per switch after its dry run.
