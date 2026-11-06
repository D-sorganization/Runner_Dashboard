# /backlog-push relay (Barbara → barb-worker, #3711 extension)

Cloud or owner sessions can request a supervised backlog push by opening an issue in the private **Barbara** repo with label `relay:backlog-push`. The barb-worker relay validates a small JSON block, checks Desk hub resource pressure, launches headless `claude -p` supervisor and lane processes, and comments with receipts. **Off by default** (`RELAY_BACKLOG_PUSH_ENABLED=0`).

## Feasibility (supervisor mode vs CLI)

The shared `/backlog-push` skill in `Repository_Management` (`skills/backlog-push/SKILL.md`, RM #2730) describes **supervisor mode** for an operator Mac: it orchestrates parallel work by opening **Claude Code / Codex sessions in the GUI** (iTerm/tmux launch helpers), not a single headless prompt on Linux.

**barb-worker** is Ubuntu 24.04 with global `@anthropic-ai/claude-code` and hub-minted `gh` ([barb-worker-vm.md](barb-worker-vm.md)) — no Claude Desktop. This relay therefore implements a **CLI lane mode on the worker**: one fixed-template `claude -p` supervisor plus `lanes` lane processes per request. Issue body text never enters the prompt; only validated JSON fields are substituted.

**RM follow-up (not in Runner_Dashboard):** document Barbara label `relay:backlog-push`, the fenced JSON schema below, and a `supervisor_cli` / Linux path in the skill so operator Desktop and barb-worker relay stay aligned.

## Issue body

Include exactly one fenced JSON block tagged `backlog-push-request`:

````markdown
## Backlog push

```backlog-push-request
{
  "repos": ["Runner_Dashboard", "Repository_Management"],
  "duration_min": 120,
  "lanes": 2
}
```
````

**Rules**

- `repos`: non-empty subset of the six fleet repos (`UpstreamDrift`, `AffineDrift`, `Gasification_Model`, `Tools`, `Repository_Management`, `Runner_Dashboard`). Unknown names or duplicates are refused.
- `duration_min`: integer **30–240** (wall-clock timeout for the whole push; processes are SIGTERM’d at deadline).
- `lanes`: integer **1–4**.
- Author and last editor must match `BARB_PANEL_RELAY_AUTHORS` (same allowlist as panel relay).
- At most `BARB_BACKLOG_PUSH_DAILY_CAP` starts per UTC day (default **2**). **Single-flight:** while a push is active, no second push starts.
- Skipped when the Desk hub reports `io_pressure` ≥ **35%** or a **disk-pressure / host-volume hold** on `GET /api/system` (if reachable); the issue gets a refusal comment with the reason.
- Prompts include: PRs under 500 lines; **never merge directly** — arm auto-merge only via `python scripts/automerge_guard.py D-sorganization/<Repo> <pr> --arm` when the exact head is green (no `--admin`); **Desk hub / Runner_Dashboard** standing hold (no deploy/restart/reconfig without Dieter); Sidekick packaging and Assessment Board flood holds.
- Lanes run via `backlog-push-spawn.py` in a **detached** user scope (`systemd-run --user --scope` or `setsid`), not as children of the panel-relay oneshot. **GitHub:** lanes use the hub fleet `gh` wrapper (re-mints App token per invocation; periodic `gh api` refresh in `backlog-push-lane.sh`); never embed tokens in prompts or logs.

## Outcome labels

| Label | Meaning |
| ----- | ------- |
| `backlog-push-started` | Accepted; supervisor + lanes launched |
| `backlog-push-done` | Finished (timeout or processes exited) |
| `backlog-push-refused` | Validation, cap, resource gate, or allowlist failure |

## Operator enable (barb-worker)

After merge (builds on panel relay installer in PR #4093 if that is still open):

```bash
sudo git -C /opt/Runner_Dashboard pull --ff-only
sudo bash /opt/Runner_Dashboard/scripts/barb-worker/install-panel-relay.sh --enable-backlog-push
```

Turn backlog push off without stopping panels: re-run the installer with `--disable-backlog-push` (panel timer stays on). Full stop: `--disable`.

Environment (systemd `barb-panel-relay.env`):

- `RELAY_BACKLOG_PUSH_ENABLED` — default `0`; set `1` to enable
- `BARB_BACKLOG_PUSH_DAILY_CAP` — default `2`
- `BARB_BACKLOG_PUSH_STATE` — default `~/.config/runner-dashboard/barb-backlog-push-relay.json`
- Reuses `BARB_PANEL_RELAY_AUTHORS`, `FLEET_API_URL`, `FLEET_API_TOKEN`

Dry-run (both relays): `bash scripts/barb-worker/panel-relay-pass.sh --dry-run` as the lane user (mints bot token; prints plans only).
