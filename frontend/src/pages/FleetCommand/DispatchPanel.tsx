/**
 * DispatchPanel.tsx — one-click staff dispatch (#1233).
 *
 * DRY: this is the Staff tab's own `Assign` form (role from the roster,
 * provider limited to the role, repo, issue / PR / prompt, machine, dry-run
 * "Preview" then "Dispatch") fed by the same `fetchRoster`. The only addition
 * is what happens after a real dispatch: a link that opens the new run in
 * the Staff tab (`/t/staff?run=<id>`), where its live tail lives.
 */
import { useState } from "react";
import { TouchButton } from "../../primitives/TouchButton";
import { AdvancedDispatchForm } from "../Staff/AdvancedDispatchForm";
import { fetchRoster } from "../Staff/staffApi";
import { staffRunHref, useResource } from "./fleetApi";
import { PanelFrame } from "./PanelFrame";

export function DispatchPanel() {
  const roster = useResource(fetchRoster, "roster");
  const [runId, setRunId] = useState<string | null>(null);

  if (roster.unavailable || roster.error || (roster.loading && !roster.data)) {
    return (
      <PanelFrame
        title="Dispatch"
        testId="fleet-dispatch"
        loading={roster.loading}
        hasData={false}
        error={roster.error}
        unavailable={roster.unavailable ? "The Staff Hub (/api/staff) is not served by this node." : null}
        onRetry={roster.reload}
      />
    );
  }

  return (
    <div className="fleet-cmd__dispatch" data-testid="fleet-dispatch">
      {runId ? (
        <div className="glass-card fleet-cmd__dispatched" role="status" data-testid="dispatch-result">
          <span>
            Dispatched run <code>{runId}</code>.
          </span>
          <a href={staffRunHref(runId)} data-testid="dispatch-run-link">
            Open in the Staff tab
          </a>
          <TouchButton onClick={() => setRunId(null)}>Dismiss</TouchButton>
        </div>
      ) : null}
      <AdvancedDispatchForm roster={roster.data} onDispatched={setRunId} />
    </div>
  );
}

export default DispatchPanel;
