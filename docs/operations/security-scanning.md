# Fleet Security Scanning: Operator Runbook

Epic [#2566](https://github.com/D-sorganization/Runner_Dashboard/issues/2566).
The design comes from panel verdicts q3 and q4 (Argon, Astra, Fable).

## What It Does

A repository opts in by adding `.oss-scanner/`, which holds `project.yaml`, a
`Dockerfile` and `threat_model.md`. The templates and validator live in
Repository_Management. The dashboard runs the scan in two phases:

1. **Phase A.** The default-branch head is downloaded as a tarball (no `.git`,
   no credentials). Its `.oss-scanner/Dockerfile` is built in a buildx builder
   on a filtered network.
2. **Phase B.** The image starts with `--network=none`, no capabilities, a
   read-only rootfs, capped tmpfs and uid 65532. The claude CLI runs on the
   dashboard host with no built-in tools. Its only tools come from the
   `scan_sandbox` MCP server, which execs argv lists inside that one container.

Findings land in the private findings store as `new`. They are never filed
publicly, and no patch is applied. An operator triages them on the Security page.

## Verification, Advisories and SARIF

- **Verification.** After a scan, a second Claude model
  (`executor.verifier_model`, default `claude-sonnet-5-5`, which must differ
  from `executor.model`) re-tests each new finding. It runs in a fresh container
  from the same image, through the same MCP-only profile, and its
  `report_verdict` tool replaces `report_finding`.
  - A reproduced finding becomes `confirmed`.
  - Every other outcome becomes `inconclusive`: not reproduced, inconclusive, or
    no verdict at all.
  - The verifier never marks anything a false positive; only an operator does.
  - Each change is in the finding's audit trail with actor
    `verifier:<model>`.
  - An operator's triage always wins over a later verdict.
  - Set `verifier_model` to `null` to turn verification off.
- **Draft advisory.** `POST /api/v1/security/findings/{id}/advisory` creates a
  **draft** GitHub security advisory with a temporary private fork, where the fix
  is developed out of public view.
  - It needs scope `security.advisory` and a `confirmed` `high` or `critical`
    finding.
  - It creates one draft per finding.
  - The dashboard's GitHub identity needs the
    _Repository security advisories: write_ permission (App) or admin on the
    repository.
  - Nothing creates advisories automatically.
- **Protected patches.** Any finding whose candidate patch touches `.github/` or
  `.oss-scanner/` reports those paths in `patch_protected_paths`. Such a patch is
  never included in an advisory and never handed to automation: CI and scanner
  configuration changes are written by a person.
- **SARIF.** `GET /api/v1/security/export/sarif` is off unless the operator
  config sets `"sarif_export": true`.
  - It includes open findings only, plus their title, root cause and location.
  - Reproduction steps and patches are never exported.
  - Upload the file to code scanning only for repositories where every reader
    may see the findings.

## Safety Rules

These are enforced in code (`backend/vuln_scan/admission.py`), not by
convention.

- **Real scans need a VM scan host.** The host must be declared
  `isolation: "vm"`, reached over `DOCKER_HOST=ssh://`, and have a
  `build_network`. A WSL distro is not a VM. ControlTower never hosts a scan.
- **CI wins.**
  - A scan starts only when the CI queue is idle and the scan host has at least
    60 GB free.
  - Both are re-polled every 30 s during phases A and B. A queued CI job aborts
    the scan.
- **The night window, one fleet-wide slot, and `usd_per_day` apply.**
- **The CLI version is pinned** (`executor.cli_version`). The CLI's
  `system/init` event must list exactly the four `scan_sandbox` tools, or the
  run is killed. A tool call outside that set kills the run too.
- **Requests can't name a SHA.** Only the default-branch head is scanned.
- **The container, image and temp directories are removed in a `finally`**,
  including on cancel.

## Enabling a Scan Host (Owner, One Time)

1. Provision a Linux VM: Hyper-V or a separate machine, _not_ a WSL distro, not
   ControlTower. Install Docker and buildx. Give the dashboard host an SSH key
   for a dedicated `scan` user in the `docker` group.
2. On the VM, review then apply the filtered network:

   ```bash
   sudo deploy/security-scan-host/setup-build-network.sh            # dry run
   sudo deploy/security-scan-host/setup-build-network.sh --apply
   ```

   Re-run it at boot (for example from a systemd unit), because the iptables
   rules do not persist.

3. In `config/security_scan.json` on the hub:

   ```json
   "scan_hosts": {
     "scanvm": { "isolation": "vm", "docker_host": "ssh://scan@scanvm", "build_network": "vs-build" }
   },
   "executor": { "cli_version": "<output of claude --version>" }
   ```

   Then set `"enabled": true` on the repositories to scan.

4. Check from the Security page, or with
   `POST /api/v1/security/scans {"repo": "Tools"}`. The default is a dry run,
   which returns every reason a real scan would be refused. Fix them until the
   list is empty, then send `"dry_run": false`.

The validation that needs the VM is deferred until the VM exists, and is tracked
on [#2574](https://github.com/D-sorganization/Runner_Dashboard/issues/2574).
It covers a real build behind the filter, a real agent run, and negative
network probes from inside phase A.

## Coverage and Scheduling (Owner Enables)

`GET /api/v1/security/coverage` lists, for every tracked repository:

- whether its contract is present and valid;
- whether it is enabled, with its priority and `full_scan_days`;
- its last real scan and last success;
- whether it is **due**: enabled, valid contract, and no success within
  `full_scan_days`.

Contracts reach the rest of the fleet in staggered waves of scaffold PRs from
Repository_Management (`maintenance_scripts/propagate_oss_scanner.py`). Each
threat model is a draft that the owner reviews before merging.

The scheduler ships **off**. To turn it on, set this in
`config/security_scan.json`:

```json
"schedule": { "enabled": true, "check_minutes": 30 }
```

Every `check_minutes` the hub picks the most urgent due repository: the lowest
`priority`, then the oldest last success. It evaluates admission without writing
a row. It starts a real scan only when every admission rule passes, so outside
the night window or while CI is busy it does nothing.

The scheduler adds no guard of its own and can bypass none. Nodes never schedule
(it checks `MACHINE_ROLE == "hub"` on every tick), and the config is re-read
every tick, so setting `enabled` back to `false` takes effect without a restart.

## Scopes

| Action                        | Scope               |
| ----------------------------- | ------------------- |
| Read posture, findings, scans | `security.read`     |
| Triage a finding              | `security.triage`   |
| Plan, start or cancel a scan  | `security.scan`     |
| Draft a GitHub advisory       | `security.advisory` |

Until the identity presets grant these scopes, only admin principals (`*`)
hold them.

## Known Limits

- Only public repositories are supported. The source is fetched from
  codeload.github.com with no token.
- A large image, such as UpstreamDrift's, can take more than 20 minutes to
  build. `build_timeout_minutes` is capped at 30.
- The executor's login is the staff seat's (`resolve_claude_config_dir`).
  `CLAUDE_CODE_OAUTH_TOKEN` is stripped by the environment allowlist.
