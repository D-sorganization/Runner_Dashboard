# barb-worker Hyper-V VM unreachable

Barb lane users (`barb-grok`, `barb-dots`, `barb-duet`) run on a dedicated **Ubuntu 24.04 Hyper-V guest** (see [barb-worker-vm.md](../agents/barb-worker-vm.md)). The hub reaches them only over **Tailscale**; lanes call back through per-user localhost proxies to the desk hub HTTPS origin.

## Detection

| Signal | Where | Meaning |
| ------ | ----- | ------- |
| `machines_offline` includes barb-worker | `GET /api/staff/summary` | Hub fleet board has not recently seen the worker’s dashboard peer (if registered) or health fan-out failed |
| `livez=fail` / `staff_summary=fail` | On VM: `sudo bash scripts/barb-worker/bootstrap.sh verify` | Token, proxy, or hub URL broken on the guest |
| Tailscale `offline` for the VM | `tailscale status` on hub or admin workstation | Guest lost tailnet (sleep, snapshot revert, or Tailscale service stopped) |
| Hyper-V state not **Running** | Host: `Get-VM` / Hyper-V Manager | Guest powered off, checkpoint restore pending, or host reboot |
| Lane units inactive | On VM: `systemctl --user status barb-lane.service` (per lane user) | Bootstrap not run, linger disabled, or crash loop |

Hub operators should **not** SSH with embedded passwords in tickets; use Tailscale SSH or the host console if tailnet is down.

## Remediation

### 1. Restore the Hyper-V guest

On the Windows host:

1. Open Hyper-V Manager (or `Get-VM -Name '<barb-worker>'`).
2. If state is Off or Paused, **Start** the VM.
3. If the guest hangs at boot, use the virtual console (not a stale RDP session) and confirm networking comes up.
4. After host updates, confirm the **Tailscale** service starts inside the guest (systemd), not only on Windows.

### 2. Restore tailnet connectivity

Inside the guest (console or working SSH):

```bash
sudo tailscale status
sudo systemctl status tailscaled
```

If needed, re-authenticate with the fleet Tailscale account (owner action). Confirm the node appears in `tailscale status` from the desk hub.

### 3. Re-verify barb-worker bootstrap

Set the live hub HTTPS origin (Tailscale Serve, no `:8321`):

```bash
export BARB_WORKER_HUB_URL='https://<desk-hub>.ts.net'
cd /opt/Runner_Dashboard   # or admin checkout
sudo -E bash scripts/barb-worker/bootstrap.sh verify
```

Expected: one line per lane user with `livez=pass` and `staff_summary=pass` (no tokens printed).

If tokens were rotated on the hub, reinstall lane token files (mode **600**) and restart lane services:

```bash
for u in barb-grok barb-dots barb-duet; do
  sudo -u "$u" env XDG_RUNTIME_DIR="/run/user/$(id -u "$u")" systemctl --user restart barb-lane.service
done
```

### 4. Hub-side confirmation

From an admin session on the hub:

- `GET /api/staff/summary` — Barb-related attention should clear; rd-threads / Barb inboxes should show lane activity again.
- Optional: `clients/fleet` doctor against `FLEET_API_URL` with each lane principal (never log bearer tokens).

## Prevention

- Enable **automatic start** for the Hyper-V VM after host reboot.
- Keep `loginctl enable-linger` for lane users (bootstrap applies this).
- After hub credential or URL changes, re-run `bootstrap.sh verify` before relying on overnight Barb lanes.

## Related

- [barb-worker VM](../agents/barb-worker-vm.md)
- [barb-lane principals](../agents/barb-lane-principals.md)
- [Phone / tailnet access](phone-access-tailnet.md)
