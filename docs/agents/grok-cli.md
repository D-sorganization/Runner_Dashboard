# Grok CLI Connection & Provider Guide

This guide covers configuring the standalone **Grok CLI** (`grok`, provider ID `grok_cli`) as a headless execution provider on fleet nodes under a SuperGrok subscription.

---

## 1. Overview & Architecture

The Grok CLI is the standalone terminal coding tool from xAI. In the Runner Dashboard architecture:

- **Provider Key:** `grok_cli` (canonical ID in `backend/agent_remediation/provider_registry.py`; aliases `grok`, `grok-cli`)
- **Executable:** `grok`
- **Execution Mode:** Local subprocess execution (`local_exec`) on fleet nodes (hub-a, node-a, node-b).
- **Unattended Execution:** Launches with `-p <prompt> --output-format stream-json --allow <allow> --deny <deny> --no-auto-update -w <workdir> --model <model>`.
- **Safety & Permissions (#1586):** Unattended runs enforce strict allow/deny boundaries. Fleet adapters **never** pass permission bypass flags such as `--yolo` or `--always-approve`.
- **Chat Mode:** Grok CLI does not currently expose a confirmed read-only inspection flag; read-only chat turns raise `ChatReadOnlyUnsupportedError` rather than risk uncontained writes.
- **Failover Chain:** Grok CLI is opt-in per role via `providers:` in `staff/roles/*.yml`. It is excluded from the default failover chain (`config/provider_failover.json`) pending auth health monitoring (#4132) and usage tracking (#4133).

### Disambiguation & Related Integrations

To prevent confusion between similar names across the fleet:

1. **Grok Bot ([grok.md](grok.md)):** Personal assistant front-door agent (**Barb**) interacting via `curl` and `fleetctl.py` with principal `agent-grok`. That guide documents assistant routing and shared memory, not the standalone coding CLI.
2. **Cursor-hosted Grok models (`cursor-agent`):** Grok models (`grok-4.7-*`, `cursor-grok-4.6-*`) executed through the Cursor CLI seat under the Cursor subscription. These do not require a separate xAI plan.
3. **Standalone Grok CLI (`grok_cli`, this guide):** The dedicated `grok` binary running directly on fleet host machines under a SuperGrok subscription.

---

## 2. Ingress & Network Architecture (ADR 0007: Local-Only)

In accordance with [ADR 0007](../adr/0007-agent-client-ingress-local-only.md), the Runner Dashboard does not support cloud-only ingress:

- The dashboard listens on local loopback (`http://127.0.0.1:8321`) or tailnet interfaces (`http://<node>:8321`).
- There is no Funnel, public ingress, connector, or outbound cloud relay path.
- The `grok` CLI runs as a local process on the fleet node; calling out to model endpoints is standard local-process outbound traffic, not cloud ingress.

---

## 3. Installation on Fleet Nodes

The `grok` binary must be installed on the host or inside WSL, accessible on the `PATH` of the service user running the dashboard daemon (`runner-dashboard.service`).

### Linux / WSL (hub-a, node-a, node-b)

Install via the official installer:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
```

Verify binary presence:

```bash
grok --version
```

Ensure the installation directory (typically `~/.local/bin` or `/usr/local/bin`) is included in `PATH` within the systemd service drop-in (`/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf`).

---

## 4. Authentication & Credentials

### SuperGrok Subscription Required

In adherence to fleet baseline standards:

- All Grok CLI runs must use a **SuperGrok subscription plan**.
- **No pay-per-use API keys (`XAI_API_KEY`)** may be configured or used on staff nodes.

### Sign-In Procedure

1. **Interactive Login:**
   On the target node as the service user (e.g. `fleet-operator`):

   ```bash
   grok login
   ```

   Follow the device-auth / browser flow to authenticate the subscription seat.

2. **Session Persistence:**
   Grok stores credentials under `~/.grok`.
   Systemd service units must include `%h/.grok` in `ReadWritePaths` so the daemon can read and refresh subscription tokens:

   ```ini
   ReadWritePaths=%h/.grok
   ```

---

## 5. Fleet Coordination & Staff Dispatch

### Coordination API

Like all fleet workers, Grok CLI tasks interact with the coordination surface:

- **List claimable jobs:** `GET /api/coordination/jobs`
- **Claim issue:** `POST /api/coordination/claims`
- **Release claim:** `POST /api/coordination/claims/release`

### Lane Rules & Branch Naming

Grok CLI currently runs under staff dispatch without a dedicated `grok/` lane prefix unless introduced. Branch naming defaults to standard staff run worktrees or role conventions (see [lane-rules-v2.md](lane-rules-v2.md)).
