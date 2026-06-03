/**
 * AlarmPanel — Overview at-a-glance fleet health summary (issue #863).
 *
 * Renders the current system state (OK / warning / critical) as a coloured
 * status header plus the list of *active* alarms: disk pressure, runners
 * offline, saturation, watchdog. It consumes the unified alert model — the
 * pure `computeFleetAlerts` rollup (issue #819) merged with the event-derived
 * alerts (`eventsToAlerts`, issue #863) — so the panel and the AlertsCenter
 * header pill always agree (DRY, single source of severity).
 *
 * Presentation only: alerts are supplied by the host. No fetch, no polling.
 */

import type { FleetAlert, FleetLevel } from "../lib/fleetAlerts";
import { alarmLevel, fleetLevelLabel } from "../lib/fleetAlerts";
import { Badge, type BadgeTone } from "../primitives/Badge";

export interface AlarmPanelProps {
  /** Unified, recomputed-every-poll alert list (rollup + event-derived). */
  alerts: ReadonlyArray<FleetAlert>;
  /** Optional deep-link callback when an alarm row is activated. */
  onNavigate?: (alertId: FleetAlert["id"]) => void;
}

const LEVEL_BADGE_TONE: Record<FleetLevel, BadgeTone> = {
  ok: "success",
  warning: "warning",
  critical: "danger",
};

export function AlarmPanel({ alerts, onNavigate }: AlarmPanelProps) {
  const level = alarmLevel(alerts);
  const headline =
    level === "ok"
      ? "All systems nominal"
      : `${alerts.length} active alarm${alerts.length === 1 ? "" : "s"}`;

  return (
    <section
      className={`alarm-panel alarm-panel--${level}`}
      aria-label="Fleet alarm summary"
    >
      <header className="alarm-panel__header">
        <span
          aria-hidden="true"
          className={`alarm-panel__dot alarm-panel__dot--${level}`}
        />
        <div>
          <div className="alarm-panel__status">
            {fleetLevelLabel(level)}
          </div>
          <div className="alarm-panel__headline">
            {headline}
          </div>
        </div>
      </header>

      {level === "ok" ? (
        <p className="alarm-panel__empty">
          No active alarms. Disk, runners, saturation and watchdog all healthy.
        </p>
      ) : (
        <ul className="alarm-panel__list">
          {alerts.map((a) => (
            <li
              key={a.id}
              className={`alarm-panel__row alarm-panel__row--${a.level}`}
            >
              <span className="alarm-panel__content">
                <span className="alarm-panel__title">
                  {onNavigate ? (
                    <button
                      type="button"
                      className="alarm-panel__navlink"
                      onClick={() => onNavigate(a.id)}
                    >
                      {a.title}
                    </button>
                  ) : (
                    a.title
                  )}
                </span>
                <span className="alarm-panel__detail">
                  {a.detail}
                </span>
              </span>
              <Badge
                className={`alarm-panel__chip alarm-panel__chip--${a.level}`}
                size="sm"
                tone={LEVEL_BADGE_TONE[a.level]}
              >
                {a.level}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
