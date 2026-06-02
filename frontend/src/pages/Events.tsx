/**
 * Events page (issue #863) — the dedicated event-log + alarm-center tab.
 *
 * Composes the durable feed (useFleetEvents) with the AlarmPanel (current
 * system state + active alarms) and the virtualized EventLog (scrollable,
 * filterable history). Mounted both as its own nav tab and — via the smaller
 * `OverviewEventSection` — on the Fleet/Overview screen.
 */

import { AlarmPanel } from "../components/AlarmPanel";
import { EventLog } from "../primitives/EventLog";
import { useFleetEvents } from "../hooks/useFleetEvents";
import type { FleetAlert } from "../lib/fleetAlerts";

export interface EventsTabProps {
  /**
   * Optional rollup alerts from the Overview (machines offline, watchdog, …)
   * so the AlarmPanel shows the *unified* model. When omitted, only the
   * event-derived alerts are shown.
   */
  rollupAlerts?: ReadonlyArray<FleetAlert>;
  onNavigate?: (alertId: FleetAlert["id"]) => void;
}

/** Merge rollup + event-derived alerts, de-duplicated by id (rollup wins). */
function unifyAlerts(
  rollup: ReadonlyArray<FleetAlert>,
  derived: ReadonlyArray<FleetAlert>,
): FleetAlert[] {
  const byId = new Map<string, FleetAlert>();
  for (const a of derived) byId.set(a.id, a);
  for (const a of rollup) byId.set(a.id, a);
  return Array.from(byId.values());
}

export function EventsTab({ rollupAlerts = [], onNavigate }: EventsTabProps) {
  const { events, alerts: derivedAlerts, error } = useFleetEvents();
  const alerts = unifyAlerts(rollupAlerts, derivedAlerts);

  return (
    <div className="events-tab" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <AlarmPanel alerts={alerts} onNavigate={onNavigate} />
      <div>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: "0 0 8px" }}>
          Event log
        </h2>
        {error ? (
          <p style={{ color: "var(--accent-yellow)", fontSize: 12, margin: "0 0 8px" }}>
            Live updates degraded: {error}. Showing retained history.
          </p>
        ) : null}
        <EventLog events={events} height={520} />
      </div>
    </div>
  );
}

/**
 * Compact Overview section: the alarm panel + a shorter event log, for the
 * Fleet/Overview screen. Reuses the same feed hook (DRY) so it always matches
 * the dedicated tab.
 */
export function OverviewEventSection({
  rollupAlerts = [],
  onNavigate,
}: EventsTabProps) {
  const { events, alerts: derivedAlerts } = useFleetEvents();
  const alerts = unifyAlerts(rollupAlerts, derivedAlerts);

  return (
    <div
      className="overview-event-section"
      style={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      <AlarmPanel alerts={alerts} onNavigate={onNavigate} />
      <EventLog events={events} height={280} />
    </div>
  );
}

export default EventsTab;
