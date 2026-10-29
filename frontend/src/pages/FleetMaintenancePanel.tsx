/**
 * FleetMaintenancePanel — drain / resume this machine's runners (#1936).
 *
 * Calls POST /api/fleet/maintenance. Both actions need a confirm step; a drain
 * that reboots the host has its own, louder confirm. Progress is read from
 * `state.maintenance` of GET /api/fleet/schedule (owned by the parent).
 */
import React, { useState } from "react";
import { legacyFetch } from "../lib/api";
import { Badge } from "../primitives/Badge";
import { TouchButton } from "../primitives/TouchButton";
import { maintenanceStatus, type MaintenanceState } from "./maintenanceStatus";

export interface FleetMaintenancePanelProps {
  maintenance?: MaintenanceState;
  onChanged?: () => void;
}

type Pending = "drain" | "resume" | null;

export function FleetMaintenancePanel({
  maintenance,
  onChanged,
}: FleetMaintenancePanelProps): React.ReactElement {
  const [pending, setPending] = useState<Pending>(null);
  const [reboot, setReboot] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const status = maintenanceStatus(maintenance);

  function cancel(): void {
    setPending(null);
  }

  async function submit(): Promise<void> {
    if (!pending) return;
    const body = pending === "drain" ? { action: "drain", reboot } : { action: "resume" };
    setBusy(true);
    setMessage(null);
    setFailed(false);
    try {
      const res = await legacyFetch("/api/fleet/maintenance", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = "HTTP " + res.status;
        try {
          const err = (await res.json()) as { detail?: unknown };
          if (typeof err.detail === "string") detail = err.detail;
        } catch {
          /* keep the status text */
        }
        throw new Error(detail);
      }
      setMessage(pending === "drain" ? "Drain started." : "Runners resumed.");
      setPending(null);
      onChanged?.();
    } catch (err: unknown) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Maintenance request failed");
    } finally {
      setBusy(false);
    }
  }

  const rebootConfirm = pending === "drain" && reboot;
  return (
    <div className="card fleet-maintenance" data-testid="fleet-maintenance">
      <div className="section-header">
        <span className="section-title">Maintenance</span>
        <Badge tone={status.tone} size="sm">
          {status.label}
        </Badge>
      </div>
      {pending === null ? (
        <div className="fleet-maintenance__actions">
          <label className="fleet-maintenance__reboot">
            <input
              type="checkbox"
              checked={reboot}
              onChange={(e) => setReboot(e.target.checked)}
              disabled={busy}
            />{" "}
            Reboot host after drain
          </label>
          <TouchButton onClick={() => setPending("drain")} disabled={busy}>
            Drain
          </TouchButton>
          <TouchButton onClick={() => setPending("resume")} disabled={busy}>
            Resume
          </TouchButton>
        </div>
      ) : (
        <div className="fleet-maintenance__confirm" role="alertdialog" aria-label="Confirm maintenance">
          <p>
            {pending === "resume"
              ? "Resume normal runner capacity on this machine?"
              : rebootConfirm
                ? "DRAIN all runners and then REBOOT this host. Busy jobs finish first (up to an hour); the host will go offline."
                : "Drain all runners on this machine? Busy jobs finish first (up to an hour); no new work starts."}
          </p>
          <TouchButton
            variant={rebootConfirm ? "danger" : "primary"}
            onClick={() => void submit()}
            disabled={busy}
            aria-label={
              pending === "resume"
                ? "Confirm resume runners on this machine"
                : rebootConfirm
                  ? "Confirm drain all runners and reboot this host"
                  : "Confirm drain all runners on this machine"
            }
            title={
              pending === "resume"
                ? "Confirm resume runners on this machine"
                : rebootConfirm
                  ? "Confirm drain all runners and reboot this host"
                  : "Confirm drain all runners on this machine"
            }
          >
            {pending === "resume"
              ? "Confirm resume"
              : rebootConfirm
                ? "Confirm drain and REBOOT"
                : "Confirm drain"}
          </TouchButton>
          <TouchButton onClick={cancel} disabled={busy}>
            Cancel
          </TouchButton>
        </div>
      )}
      {message ? (
        <div role={failed ? "alert" : "status"} className="fleet-maintenance__message">
          {message}
        </div>
      ) : null}
    </div>
  );
}

export default FleetMaintenancePanel;
