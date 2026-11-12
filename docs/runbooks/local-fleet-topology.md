# Local fleet topology (machine registry)

Runner Dashboard reads fleet machine identity from YAML. The public repository
ships **no live fleet** in `backend/machine_registry.yml` (empty `machines: []`);
the full schema example is `backend/machine_registry.example.yml`. Live topology
must live on each host, outside git.

## One-time migration (each hub and node)

**Do this before auto-deploy pulls the first sanitize slice** (or immediately
after, before restarting the dashboard).

1. Copy the **current live registry from the deployed install tree** (not from
   git after sanitize merges):

   ```bash
   INSTALL="${RUNNER_DASHBOARD_DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
   mkdir -p ~/.config/runner-dashboard
   cp "$INSTALL/backend/machine_registry.yml" ~/.config/runner-dashboard/machine_registry.yml
   ```

   That path is the dashboard checkout used by `runner-dashboard.service`
   (`WorkingDirectory` / `deploy/setup.sh`, typically
   `~/actions-runners/dashboard`). This is the only backup location named in
   repo docs: the file on disk **before** the public repo stops shipping live
   topology. If you already pulled an empty shipped registry, recover from your
   own backup or `git show <pre-sanitize-sha>:backend/machine_registry.yml` on
   the install tree.

2. Optionally enforce the local file at startup (recommended on hubs after
   slice 09 is live):

   ```bash
   # in ~/.config/runner-dashboard/env (service EnvironmentFile)
   RUNNER_DASHBOARD_REQUIRE_LOCAL_REGISTRY=1
   ```

3. Alternatively, set an explicit path:

   ```bash
   export MACHINE_REGISTRY_PATH=/path/to/machine_registry.yml
   ```

4. Restart the dashboard service (`runner-dashboard.service` or
   `./start-dashboard.sh`).

5. Confirm federation: open **Fleet** in the UI or `GET /api/fleet/status` and
   verify nodes resolve with the expected registry metadata.

The loader checks, in order:

1. `MACHINE_REGISTRY_PATH` when set
2. `~/.config/runner-dashboard/machine_registry.yml` when the file exists
3. `backend/machine_registry.yml` (empty documentation stub for dev clones)

If step 3 is an empty shipped file, the dashboard still starts; fleet metadata
comes from live probes only until the local registry is installed. Loading
**example** machines from the shipped path fails closed.

## Example template

`backend/machine_registry.example.yml` documents RFC-style example hosts
(`hub-a`, `node-b`), `*.fleet-example.ts.net` URLs, and `100.64.254.0/24`
addresses.

## CI guard

`tests/test_no_private_topology_in_repo.py` (final slice PR) fails when tracked
files contain private host labels, non-example `*.ts.net` names, consumer email
domains, or CGNAT addresses outside documented example subnets.
