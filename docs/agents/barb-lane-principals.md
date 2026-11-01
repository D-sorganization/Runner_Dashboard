# Barb lane principals (#3647)

Every Barb is a full peer (owner decision 2026-10-31). Each lane authenticates as its **own**
principal from `config/agent_principals.yml`. The generic `agent-claude`, `agent-codex`,
`agent-gemini` and `agent-cursor` principals stay narrow and must not be used by a Barb lane.

| Lane | Principal | Where the lane reads its token |
| --- | --- | --- |
| rd-barb watchdog (hub) | `rd-barb` | `FLEET_API_TOKEN` in `~/.config/runner-dashboard/rd-barb.env` |
| Grok Barb | `agent-grok` | `FLEET_API_TOKEN` (Grok Bot box) |
| Grok Barb on barb-worker VM | `agent-grok-vm` | `FLEET_API_TOKEN` for the `barb-grok` user |
| Duet Barb | `agent-duet` | `FLEET_API_TOKEN` for the `barb-duet` user |
| Claude Barb (`/barb`, `claude_barb_hook.py`) | `agent-claude-barb` | `FLEET_API_TOKEN`, else `RD_THREADS_TOKEN_FILE`, else `~/.config/runner-dashboard/fleet-token` |
| Codex Barb (`codex-rd-barb`) | `agent-codex-barb` | same lookup as above |
| Cursor Barb (`cursor_barb_hook.py`) | `agent-cursor-barb` | same lookup as above |
| Antigravity Barb (`agy_barb_hook.py`) | `agent-agy-barb` | same lookup as above |

`scripts/barb_cli.py` and `clients/fleet/fleet_client.py` read `FLEET_API_TOKEN` first; `barb_cli`
falls back to `RD_THREADS_TOKEN_FILE` or `~/.config/runner-dashboard/fleet-token`. Put each lane's token
in that lane's own environment or file, not a shared one, so audit shows which Barb acted.

## GitHub App tokens on barb-worker VMs (#3657)

VM users must not store long-lived GitHub PATs. With the lane's `FLEET_API_TOKEN` (same as rd-threads), mint short-lived fleet App installation tokens from the hub:

- `POST /api/github/app-token` with optional JSON `{"repository": "Runner_Dashboard"}` (bare name or `D-sorganization/...`).
- Scope required: `github.app_token` (granted to Barb lane principals in `config/agent_principals.yml`).

Client helpers (never log or commit tokens):

```bash
Bootstrap (`scripts/barb-worker/bootstrap.sh`, #3659) runs `scripts/barb-worker/install-hub-fleet-gh.sh`
per lane user after `Runner_Dashboard` is cloned. Manual install:

export GH_TOKEN="$(python scripts/barb_cli.py gh-token)"
# optional repo scope for git operations:
export BARB_GITHUB_REPO=Runner_Dashboard

git config --global credential.https://github.com.helper \
  '!python /path/to/Runner_Dashboard/scripts/barb_github_hub_token.py fill'
```

Set `FLEET_API_URL` to the Desk hub if it is not the default dashboard URL.

Tokens are minted by the owner: `POST /api/admin/principals/<id>/token` (admin bearer). Never print,
log or paste a token. After the hub restarts, the hub stores new tokens under
`~/.config/runner-dashboard/agent-tokens/<id>.token`.

## Chat turns

A dashboard chat turn runs the provider CLI in read-only mode on purpose (#1484). The turn has no shell,
and a writable chat CLI is not enabled. Barbs act through their `staff-actions` proposals instead. When
the owner (`staff.approve`) asks a Barb role (`barb` or any `*-barb`), every proposal at HIGH risk or
below, including `staff.hold` and `staff.unhold`, runs immediately under the owner's principal
(`backend/staff/owner_requests.py`). CRITICAL and owner-only actions still need a tap. For shell,
push and merge work, a Barb dispatches a staff run, or acts from its own CLI lane with its principal.
