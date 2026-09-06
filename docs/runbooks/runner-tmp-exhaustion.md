# Runbook: runner `/tmp` exhaustion (ENOSPC during `pip install`)

Tracking: Repository_Management#1489 (report), #1495 (closed duplicate),
Runner_Dashboard#1155 (GC), Runner_Dashboard PR `readiness/p1-1489-tmp-gc`
(per-runner `TMPDIR`). Program: #1505, Phase 1 item #1511.

## Symptom

A required check (`quality-gate`, `security-scan`, `CI Standard`) fails at the
dependency-install step and **every real check reports `skipped`**:

```
ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device:
       '/tmp/pip-ephem-wheel-cache-zd6cxrbj'
```

or, from `ensurepip`, an unexplained `returned non-zero exit status 1`. Re-running
moves the job to another runner and fails the same way if that host is also full.
The PR looks broken; the host is.

Since #1489, `scripts/ci/pip_install_resilient.sh` stops after the first
`[Errno 28]` and prints `::error title=Runner out of disk::` plus `df -h` of
`$TMPDIR` — if you see that annotation, this runbook applies. If you see four
retries, the repo is running an older wrapper.

## Why

Self-hosted runners default `TMPDIR` to `/tmp`. On OGLaptop (`Ubuntu` distro)
`/tmp` is a **24 GB RAM-backed tmpfs**; on ControlTower-Runner and DeskComputer
it is on the root disk (hundreds of GB free), so the tmpfs host is the one that
fills. Cancelled or crashed jobs orphan `pip-install-*`, `pip-build-env-*`,
`pip-ephem-wheel-cache-*`, `node-compile-cache`, `tmp*` scratch at ~1–2 GB/h
under load. `systemd-tmpfiles-clean` (10-day TTL) loses that race, and until
2026-09-01 the fleet GC (`runner-cleanup.sh`) did not look at `/tmp` at all.

## Check (read-only, from a workstation on the tailnet)

```bash
# which host is the failing job on?  -> "Runner name:" in the job log header
gh run view -R D-sorganization/<Repo> <run-id> --log 2>/dev/null | grep -m1 "Runner name"

# free space where pip writes, on that host
ssh <host> 'df -h /tmp; ls -d /tmp/pip-* /tmp/tmp* 2>/dev/null | wc -l'
# is the GC actually running?
ssh <host> 'systemctl list-timers runner-disk-guard.timer runner-cleanup.timer --no-pager;
            systemctl status runner-cleanup.service --no-pager | head -5;
            tail -20 /var/log/runner-cleanup/*.log 2>/dev/null'
# per-runner TMPDIR relocation applied?
ssh <host> 'grep -H ^TMPDIR= ~/actions-runners*/runner-*/.env 2>/dev/null || echo "TMPDIR not relocated"'
```

`/tmp` ≥ 75 % on a tmpfs host, or a `runner-cleanup.service` whose last result
is `exit-code` with an empty journal, are both actionable.

**WSL distro names are per-host — parameterize every scripted step**
(RD#1159, measured `wsl -l -q` 2026-09-03):

| host         | WSL distros                                                                           |
| ------------ | ------------------------------------------------------------------------------------- |
| OGLaptop     | `Ubuntu`                                                                              |
| ControlTower | `ControlTower-Runner`, `ControlTower-SSD` (the corrupt one, RM #1453), `OllamaServer` |
| DeskComputer | `Ubuntu-22.04`                                                                        |

`wsl -d Ubuntu` probes against ControlTower/DeskComputer return nothing for
exactly this reason; passing the distro explicitly
(`ssh <host> "wsl -d <distro> -- <command>"`) is required for any scripted
step on this runbook.

## Fix (owner action on the host)

1. **Reclaim now** — run the fleet GC by hand; it only removes allow-listed
   litter older than the age window (30 min once `/tmp` ≥ 75 %):

   ```bash
   sudo /usr/local/bin/runner-cleanup --disk-guard        # add --dry-run first if unsure
   df -h /tmp
   ```

   If the installed copy predates Runner_Dashboard#1155 (no `cleanup_tmp` in
   `grep -c cleanup_tmp /usr/local/bin/runner-cleanup`), redeploy from the
   Runner_Dashboard checkout: `sudo bash deploy/install-runner-maintenance.sh`.

   **Hosts without a Runner_Dashboard checkout** (RD#1159: on OGLaptop neither
   `~/Runner_Dashboard`, `~/Repositories-WSL/Runner_Dashboard` nor
   `~/repos/Runner_Dashboard` exists, and `install-runner-maintenance.sh`
   resolves `PROJECT_ROOT` from its own location). Fetch the two scripts by
   URL, back up, install, and run the TMPDIR configurator standalone:

   ```bash
   gh api repos/D-sorganization/Runner_Dashboard/contents/deploy/runner-cleanup.sh --jq .content | base64 -d | sudo tee /tmp/runner-cleanup.sh >/dev/null
   gh api repos/D-sorganization/Runner_Dashboard/contents/deploy/configure-runner-tmpdir.sh --jq .content | base64 -d | sudo tee /tmp/configure-runner-tmpdir.sh >/dev/null
   gh api repos/D-sorganization/Runner_Dashboard/contents/deploy/clean-stale-shell-profiles.sh --jq .content | base64 -d | sudo tee /tmp/clean-stale-shell-profiles.sh >/dev/null
   sudo cp /usr/local/bin/runner-cleanup /usr/local/bin/runner-cleanup.bak.$(date +%Y%m%d%H%M%S)
   sudo install -m 0755 /tmp/runner-cleanup.sh /usr/local/bin/runner-cleanup
   sudo install -m 0755 /tmp/clean-stale-shell-profiles.sh /usr/local/bin/clean-stale-shell-profiles
   # Prune stale cargo/env source lines from shell profiles (RD#1159)
   /usr/local/bin/clean-stale-shell-profiles
   # the installer's only action for runner-cleanup is the install above;
   # the systemd units/timers already exist. Then run the TMPDIR script standalone:
   sudo RUNNER_USER=$USER bash /tmp/configure-runner-tmpdir.sh --dry-run
   sudo RUNNER_USER=$USER bash /tmp/configure-runner-tmpdir.sh
   ```

2. **Re-run the failed job** (`gh run rerun --failed <run-id>`). Do not push an
   empty commit; the PR was never the problem.

3. If `runner-cleanup.service` shows `Result=exit-code` with no journal output,
   run it in the foreground once (`sudo /usr/local/bin/runner-cleanup --dry-run`)
   and file the error against Runner_Dashboard — a silently failing daily
   hygiene unit is a second incident, not a detail.

## Prevention (what is in the repos, and the one-time host step)

| Control                                                                                                     | Where                                                                                       | State                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `/tmp` litter GC, hourly (`--disk-guard`) + daily, allow-listed patterns, 6 h window, 30 min under pressure | Runner_Dashboard `deploy/runner-cleanup.sh` (`cleanup_tmp`)                                 | merged (RD#1155); deployed and verified on OGLaptop 2026-09-03 — ControlTower/DeskComputer deployment **unverified** (RD#1159) |
| `tmp*` / `pymp-*` patterns added; GC extended to each runner's relocated `TMPDIR` while idle                | Runner_Dashboard `deploy/runner-cleanup.sh` (`cleanup_litter_in`, `cleanup_runner_tmpdirs`) | this change                                                                                                                    |
| `TMPDIR=<runner>/_work/_tmp` per runner, so scratch lands on the data disk, never in RAM                    | Runner_Dashboard `deploy/configure-runner-tmpdir.sh`                                        | this change — **needs the host step below**                                                                                    |
| Fail fast on ENOSPC with a host diagnosis instead of four retries                                           | RM `scripts/ci/pip_install_resilient.sh`                                                    | this change                                                                                                                    |

**Host step (once per host, owner):**

```bash
cd ~/Runner_Dashboard && git pull
sudo bash deploy/install-runner-maintenance.sh                # installs the new runner-cleanup
sudo RUNNER_USER=$USER bash deploy/configure-runner-tmpdir.sh --dry-run
sudo RUNNER_USER=$USER bash deploy/configure-runner-tmpdir.sh
# runners read .env at start-up; restart when idle (or let the nightly full pass bounce them)
sudo systemctl restart 'actions.runner.*.service'
```

Verify from any job on that host: `echo $TMPDIR` should print
`.../runner-N/_work/_tmp`, and `df -h /tmp` should stay flat under load.

## Not done / open

- Surfacing `/tmp` free space per runner in the Runner Dashboard (#1489 item 4)
  is not implemented; the disk-guard log line `tmp cleanup done tmp_used=…` is
  the only signal today.
- ControlTower's original `runner-3` host distro (`ControlTower-SSD`) is the
  corrupt one (RM #1453) and is not exposed to this failure; its replacement
  has `/tmp` on disk.
- OGLaptop `~/.profile` sources 12 deleted cargo env files: resolved in
  Runner_Dashboard#1159 by providing `deploy/clean-stale-shell-profiles.sh`
  and integrating it into `install-runner-maintenance.sh` and the curl/URL-based
  host deployment steps.
