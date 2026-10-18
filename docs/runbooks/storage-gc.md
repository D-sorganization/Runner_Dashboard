# Fleet Storage GC: Install, Verify, Repair

**Owner:** `deploy/install-storage-gc.sh` is the _only_ thing that writes
`/usr/local/bin/runner-cleanup` and the `runner-cleanup` / `runner-disk-guard`
service + timer pairs (#1925). `install-runner-maintenance.sh` delegates to it.
`deploy/fleet_storage_gc.py` rolls it out to every host over Tailscale.

## Why This Exists

On 2026-10-11 every runner host was running a _different_ cleanup, and nothing noticed:

- **OGLaptop** ran an old fork with no rustup reaper. It leaked 35k `~/.rustup/tmp` directories (23 GB), and Windows `C:` was down to 0.6 GB free.
- **DeskComputer** had the right binary, but a drop-in (`30-bandwidth-policy.conf`) silently replaced `ExecStart` with a frozen fork.
- **brick** and **raspberrypi** had no GC at all.

The cause was three installers writing the same paths with no check of what actually ended up installed. The fix has three parts:

- one owner for those paths;
- a `--verify` contract that fails loudly;
- a single rollout across the fleet.

## Routine Commands (From a Runner_Dashboard Checkout on Any Fleet Host)

```bash
python deploy/fleet_storage_gc.py --list          # inventory (registry + config/storage-gc-workstations.json)
python deploy/fleet_storage_gc.py --verify-only   # read-only drift check, exit 1 on any drift
python deploy/fleet_storage_gc.py --dry-run       # what an install would change
python deploy/fleet_storage_gc.py                 # install + verify everywhere
python deploy/fleet_storage_gc.py --hosts brick   # one host
```

Per-host statuses:

| Status         | Meaning                                                                                                            |
| -------------- | ------------------------------------------------------------------------------------------------------------------ |
| `ok`           | The host passed `--verify`.                                                                                        |
| `drift`        | At least one check failed; the failed checks are listed.                                                           |
| `precondition` | The installer refused to run (exit 2); the reason is in the output.                                                |
| `needs-sudo`   | No passwordless sudo on this host. The tool prints one command for you to run, and you type the password yourself. |
| `unreachable`  | SSH failed.                                                                                                        |

## What `--verify` Checks

| Check                   | Fails when                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------- |
| `binary_hash`           | The installed binary's sha256 differs from the canonical `runner-cleanup.sh`.                     |
| `stamp`                 | `/usr/local/share/runner-cleanup/STAMP` is missing or stale.                                      |
| `unit:*`                | Any of the four unit files is missing.                                                            |
| `execstart`             | Either service does not run `/usr/local/bin/runner-cleanup` (or that binary with `--disk-guard`). |
| `no_execstart_override` | Any `*.service.d/*.conf` drop-in sets `ExecStart=`.                                               |
| `timer_enabled:*`       | Either timer is not enabled.                                                                      |

When the installer finds an `ExecStart=` override in a drop-in, it neutralises it but does not delete it:

- the original file is kept as `<file>.bak-storage-gc`;
- its `Environment=` tuning stays in force.

## Profiles

- **`runner`**: CI hosts listed in `backend/machine_registry.yml`, from live pools that have a `wsl_distro`.
- **`workstation`**: native-Linux machines listed in `config/storage-gc-workstations.json`. This profile sets three things:
  - `CLEANUP_TMP=0`, so `/tmp` is never reaped under a logged-in user's apps;
  - `AGGRESSIVE_ON_PRESSURE=0`;
  - `PRUNE_DOCKER_CONTAINERS=0`, because stopped containers are user state, not CI litter.

To add a host, edit the registry or the workstation list. Never hard-code a host anywhere else.

## Docker Image Lifecycle and the `keep` Label

Images labelled `keep=true` (e.g. `docker build --label keep=true` for heavy base/sim images) are protected during disk pressure:

- **Tagged images**: `docker image prune --all` spares any image carrying `label=keep`.
- **Dangling images (untagged)**: Reclaimed regardless of the `keep` label (#1981). Untagged superseded builds cannot be referenced by name; the `keep` label protects tagged images only. Routine maintenance reclaims dangling images older than `DOCKER_DANGLING_UNTIL` (default `6h`), and aggressive cleanup reclaims all dangling images immediately.

## Known Limits

- **WSL `ext4.vhdx` files do not shrink when the guest frees space.** Reclaiming that space on the Windows side is a separate step: see [wsl-vhdx-compaction.md](wsl-vhdx-compaction.md).
- **A small root disk can still be full of user data.** raspberrypi sat at 97% after GC, because its usage is user data under `/home` that is not GC-eligible. That needs a human decision; the GC must not delete it.
