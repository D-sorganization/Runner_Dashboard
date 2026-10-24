# Local fleet topology (machine registry)

Runner Dashboard reads fleet machine identity from YAML. The public repository
ships **example** names, MagicDNS labels, and Tailscale CGNAT addresses only.
Live topology must live on each host, outside git.

## One-time migration (each hub and node)

1. On the host that already runs the dashboard, copy the previous registry
   content into the canonical local path (create the directory if needed):

   ```bash
   mkdir -p ~/.config/runner-dashboard
   cp backend/machine_registry.yml ~/.config/runner-dashboard/machine_registry.yml
   ```

   Replace the copied file with your **real** machine names, Tailscale IPs, and
   dashboard URLs before restarting the service.

2. Alternatively, set an explicit path in the service environment:

   ```bash
   export MACHINE_REGISTRY_PATH=/path/to/machine_registry.yml
   ```

3. Restart the dashboard service (`runner-dashboard.service` or your local
   `./start-dashboard.sh`).

4. Confirm federation: open **Fleet** in the UI or `GET /api/fleet/status` and
   verify nodes resolve with the expected registry metadata.

The loader checks, in order:

1. `MACHINE_REGISTRY_PATH` when set
2. `~/.config/runner-dashboard/machine_registry.yml` when the file exists
3. `backend/machine_registry.yml` (example topology for dev clones)

## Example template

`backend/machine_registry.example.yml` documents the full schema with RFC-style
example hosts (`hub-a`, `node-b`), `*.fleet-example.ts.net` URLs, and
`100.64.254.0/24` addresses.

## CI guard

`tests/test_no_private_topology_in_repo.py` fails if tracked files contain
private host labels, non-example `*.ts.net` names, consumer email domains, or
CGNAT addresses outside the documented example subnets.
