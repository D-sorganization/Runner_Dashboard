# Resource shaping trial (issue #4312)

Single-host, **opt-in** trial under epic #4306. Checked-in defaults keep
`config/resource-shaping-trial.json` **`"enabled": false`**. No fleet auto-deploy
applies this until an operator enables it on one host and records a go/no-go.

## Phase 1 — systemd weights (no WSL restart)

Per-runner soft shaping via systemd drop-ins:

- `CPUWeight=20`, `IOWeight=20`, `Nice=10`
- `Slice=runners.slice` (unit template in `deploy/systemd-dropins/runners.slice`)
- **Excluded:** `CPUQuota`, `MemoryMax` (hard caps stay out of scope)

```bash
# On the trial host only — edit host_label, then enable:
sudo install -m 0644 config/resource-shaping-trial.json /etc/runner-dashboard/resource-shaping-trial.json
# Set "enabled": true in that file, then:
sudo RESOURCE_SHAPING_TRIAL_CONFIG=/etc/runner-dashboard/resource-shaping-trial.json \
  deploy/configure-runner-resource-shaping.sh
sudo systemctl daemon-reload
# Restart idle runner units when quiet:
sudo systemctl restart 'actions.runner.*.service'   # only when idle
```

Optional phase 2: set `systemd.memory_high_enabled` to `true` in the same JSON and
re-run the configure script. The installer renders the JSON `systemd` settings;
weights, nice, slice name and memory high are configurable. Setting
`memory_high_enabled` back to `false` removes that phase’s drop-in.
Templates intentionally omit `MemoryMax`.

## Phase 2 — WSL `.wslconfig` (separate; owner approval)

See `config/wslconfig-resource-shaping-trial.example` for `autoMemoryReclaim=gradual`
under `[experimental]` and optional `memory=` / `processors=` limits under
`[wsl2]`, per the [WSL configuration reference](https://learn.microsoft.com/en-us/windows/wsl/wsl-config). Requires a planned `wsl --shutdown`
window. **Not applied by scripts in this repo** — manual merge only after approval.

## Measurement harness (one-week baseline vs trial)

Collect workflow-run outcomes for the trial host for **7 days baseline** and
**7 days with shaping enabled**. Export JSON bundles:

```json
{
  "workflow_runs": [
    {
      "conclusion": "success",
      "run_started_at": "2026-11-01T10:00:00Z",
      "updated_at": "2026-11-01T10:12:00Z"
    }
  ],
  "interactive_latency_ms": [95, 110, 102]
}
```

`interactive_latency_ms` is optional (e.g. sampled UI/API round-trip or host-metrics
proxy during interactive use).

```bash
python -m deploy.resource_shaping_trial \
  --config config/resource-shaping-trial.json \
  --baseline /path/baseline.json \
  --trial /path/trial.json \
  --report-out /path/go-no-go.md \
  --json-out /path/go-no-go.json
```

Exit code `0` = **go**, `1` = **no-go** (or use the markdown report for
**inconclusive** when sample counts are too low).

## Go / no-go criteria (defaults in config)

| Signal | Default tolerance |
|--------|-------------------|
| Failure rate vs baseline | ≤ +0.5 pp |
| Timeout rate vs baseline | ≤ +0.25 pp |
| Job duration p95 | ≤ +15% |
| Interactive latency p95 | ≤ +20% |
| OOM-like failures | Must not increase |

Record the rendered report (numbers + decision) on the trial issue before fleet-wide rollout.

## Rollback

Set `"enabled": false` and re-run `configure-runner-resource-shaping.sh` to remove
trial drop-ins, then `daemon-reload` and restart idle runners.