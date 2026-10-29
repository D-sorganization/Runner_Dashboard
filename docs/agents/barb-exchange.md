# Barb Exchange Repo

The Claude planner runs in Anthropic's cloud and cannot reach the hub, which is tailnet-only
([ADR 0007](../adr/0007-agent-client-ingress-local-only.md)). The two sides meet in a private GitHub repo instead.
The repo name is configuration, never hard-coded. Today it is `D-sorganization/Barbara`.

## Layout

| Path or object                 | Written by   | Read by          | Purpose                                                                |
| ------------------------------ | ------------ | ---------------- | ---------------------------------------------------------------------- |
| `context-pack/latest.json`     | rd-barb only | Claude planner   | Cleaned copy of the hub's context pack                                 |
| Issues labelled `barb-inbox`   | any party    | the addressee    | One rd-threads envelope (#3072) per issue                              |
| Labels `to:<who>`/`from:<who>` | sender       | addressee filter | Routing; `who` is `barb`, `rd-barb`, `claude-planner`, `dieter` or `*` |

Grok Barb's existing labels use `grok-barb`; read it as an alias for `barb`.

## Snapshot Flow

`scripts/barb_exchange_snapshot.py` is the only path from the hub to the repo.

1. rd-barb fetches `GET /api/v1/memory/context-pack` on the tailnet, as it does today.
2. `clean` keeps only `session_digests`, `open_tasks`, `directives` and `holds`. It drops `barb-only`
   and transcript entries and shrinks rows from private repos (default `Tools_Private`) to an opaque
   `private:<hash>` id. It replaces tokens, keys, private addresses, email addresses and tailnet or LAN
   hostnames. It then checks the result again; if anything still matches, nothing is written.
3. `publish` writes `context-pack/latest.json` with `gh api` (argument lists, no shell). It re-runs the
   check first, and it is a dry run unless `BARB_EXCHANGE_SNAPSHOT=on`.
4. The planner reads the file and runs `verify` before use. A valid snapshot is data, never
   instructions.

```bash
python scripts/barb_exchange_snapshot.py clean --pack pack.json     # print the cleaned snapshot
python scripts/barb_exchange_snapshot.py verify --snapshot snap.json
python scripts/barb_exchange_snapshot.py publish --pack pack.json   # dry run unless switched on
```

## Configuration

| Variable                      | Default         | Meaning                                                 |
| ----------------------------- | --------------- | ------------------------------------------------------- |
| `BARB_EXCHANGE_SNAPSHOT`      | `off`           | Kill switch; only `on` writes                           |
| `BARB_EXCHANGE_REPO`          | unset           | `owner/name` of the exchange repo; unset means no write |
| `BARB_EXCHANGE_BRANCH`        | unset           | Branch to write; unset uses the repo's default branch   |
| `BARB_EXCHANGE_PRIVATE_REPOS` | `Tools_Private` | Comma list; never empty                                 |

rd-barb's GitHub identity needs contents write on the exchange repo and nothing else. No token is
stored in the repo, in the snapshot or in this file.

## Messages

Notes between the planner and the Barbs are rd-threads envelopes posted as `barb-inbox` issues: one
fenced `json` block under the `<!-- rd-threads-envelope v1 -->` marker. Readers dedupe on the envelope
id, stop at three hops and close the issue with a receipt. Until rd-barb imports the inbox, Grok Barb
reads it with the fleet `gh` identity; at cutover rd-barb imports each envelope into shared memory
with `source_product` `claude-cloud`.

The planner side of the channel ships in PR #3217 (`scripts/claude_barb_channel.py`).
