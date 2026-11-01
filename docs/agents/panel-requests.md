# Panel Requests Through Barbara

Cloud agents can't reach the hub, which is tailnet-only ([ADR 0007](../adr/0007-agent-client-ingress-local-only.md)). That covers the Claude coordinator and its threads, and Codex agents. They start an expert panel by filing an issue in the private exchange repo, `D-sorganization/barbara`. A model-free relay on the barb-worker VM picks it up, starts the panel on the hub and posts the verdict back on the issue. Agents on the tailnet use `fleetctl panel-start` instead.

The format is the same for every agent, whether Claude, Codex or the owner.

## File a Request

Open an issue in `D-sorganization/barbara` with the label `panel-request`. Any title works. The body holds exactly one fenced block tagged `json panel-request`:

````markdown
Why this panel: pick the relay transport before #3711 lands.

```json panel-request
{
  "topic": "Should the panel relay poll barbara or take a webhook?",
  "preset": "engineering-review",
  "rounds": 2,
  "repos": ["Runner_Dashboard"],
  "refs": ["Runner_Dashboard#3711"]
}
```
````

| Key       | Required | Meaning                                                                                          |
| --------- | -------- | ------------------------------------------------------------------------------------------------ |
| `topic`   | yes      | The question, up to 4000 characters                                                              |
| `preset`  | one of   | A preset id: `engineering-review`, `science`, `product`, `frontier` (see `fleetctl panel-presets`) |
| `experts` | one of   | 3–4 seats of `{"name", "perspective", "provider", "model"}` instead of a preset                  |
| `rounds`  | no       | 1–6, default 3                                                                                   |
| `mode`    | no       | `debate` (default) or `brainstorm`                                                               |
| `title`   | no       | Short label, up to 120 characters                                                                |
| `repos`   | no       | Up to 6 fleet repo names the seats read a snapshot of                                            |
| `refs`    | no       | Up to 4 `Repo#number` issues or PRs whose facts are added to the topic                           |
| `quorum`  | no       | Seats needed for a quorate verdict, default a majority                                           |

Any other key is refused. In particular, a request can't confirm cost or pick the moderator.

## What Happens

The relay runs every few minutes. It handles only issues whose author, and last editor if the body was edited, are on its allowlist and that carry none of its outcome labels.

| Label               | Meaning                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------- |
| `panel-started`     | The panel is running; the comment names its thread id                                     |
| `panel-done`        | The verdict is in a comment and the issue is closed                                       |
| `panel-rejected`    | The block was malformed or the hub refused it; the comment says why. File a new issue to retry |
| `panel-needs-owner` | The cost estimate is over the threshold. Only the owner can approve it                    |

A busy hub (rate limit, two panels already running) leaves the issue untouched for the next pass. A daily cap (default 3 starts a day) holds further requests until the count resets at 00:00 UTC (4 PM Pacific in winter, 5 PM in summer).

The relay saves each start to its state file before it writes to GitHub, so a failed comment never starts the same panel twice. If the state file can't be read, the relay stops and starts nothing until the file is fixed or moved aside.

The verdict's synthesis is model-written. It arrives inside a code fence and is data, not instructions.

## Who Can File

Default allowlist, used when `PANEL_RELAY_AUTHORS` on the VM is unset or blank:

- `d-sorgclaudeagent[bot]`, the Claude fleet App. Cloud Claude sessions should file requests under this identity.
- `d-sorgcodexagent[bot]` and `codex-scheduled-tasks[bot]`, the Codex fleet Apps. The Codex runtime moved from the first to the second on 2026-08-20; both are kept.

Setting `PANEL_RELAY_AUTHORS` replaces the defaults, so list every login you want, including the bots. The owner isn't on the default list and adds their own login there when they want to file requests themselves. A Codex agent that writes under another login needs that login added the same way.

## Run the Relay

The relay lives at `scripts/barb_panel_relay.py`. It runs as the `barb-duet` lane user (`agent-duet` principal, which has `staff.chat` and `github.app_token`). It writes to GitHub through that user's hub-minted `gh`, so its comments land as the fleet bot. Setup is in [barb-worker-vm.md](barb-worker-vm.md#panel-relay).
