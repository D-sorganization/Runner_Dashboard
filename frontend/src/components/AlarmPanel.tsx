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

import type { CSSProperties } from "react";
import type { FleetAlert, FleetLevel } from "../lib/fleetAlerts";
import { alarmLevel, fleetLevelLabel } from "../lib/fleetAlerts";

export interface AlarmPanelProps {
  /** Unified, recomputed-every-poll alert list (rollup + event-derived). */
  alerts: ReadonlyArray<FleetAlert>;
  /** Optional deep-link callback when an alarm row is activated. */
  onNavigate?: (alertId: FleetAlert["id"]) => void;
}

const LEVEL_COLOR: Record<FleetLevel, string> = {
  ok: "var(--accent-green)",
  warning: "var(--accent-yellow)",
  critical: "var(--accent-red)",
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
      style={panelStyle(level)}
    >
      <header className="alarm-panel__header" style={headerStyle}>
        <span
          aria-hidden="true"
          className="alarm-panel__dot"
          style={{ ...dotStyle, background: LEVEL_COLOR[level] }}
        />
        <div>
          <div className="alarm-panel__status" style={statusStyle}>
            {fleetLevelLabel(level)}
          </div>
          <div className="alarm-panel__headline" style={headlineStyle}>
            {headline}
          </div>
        </div>
      </header>

      {level === "ok" ? (
        <p className="alarm-panel__empty" style={emptyStyle}>
          No active alarms. Disk, runners, saturation and watchdog all healthy.
        </p>
      ) : (
        <ul className="alarm-panel__list" style={listStyle}>
          {alerts.map((a) => (
            <li
              key={a.id}
              className={`alarm-panel__row alarm-panel__row--${a.level}`}
              style={rowStyle(a.level)}
            >
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="alarm-panel__title" style={{ fontWeight: 600 }}>
                  {onNavigate ? (
                    <button
                      type="button"
                      className="alarm-panel__navlink"
                      onClick={() => onNavigate(a.id)}
                      style={navLinkStyle}
                    >
                      {a.title}
                    </button>
                  ) : (
                    a.title
                  )}
                </span>
                <span
                  className="alarm-panel__detail"
                  style={{ display: "block", color: "var(--text-secondary)", fontSize: 12 }}
                >
                  {a.detail}
                </span>
              </span>
              <span
                className={`alarm-panel__chip alarm-panel__chip--${a.level}`}
                style={chipStyle(a.level)}
              >
                {a.level}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function panelStyle(level: FleetLevel): CSSProperties {
  return {
    border: "1px solid var(--border)",
    borderLeft: `4px solid ${LEVEL_COLOR[level]}`,
    borderRadius: 10,
    background: "var(--bg-secondary)",
    padding: 16,
  };
}

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  marginBottom: 12,
};

const dotStyle: CSSProperties = {
  borderRadius: "50%",
  height: 14,
  width: 14,
  flexShrink: 0,
};

const statusStyle: CSSProperties = {
  fontSize: 16,
  fontWeight: 700,
  color: "var(--text-primary)",
};

const headlineStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--text-secondary)",
};

const emptyStyle: CSSProperties = {
  color: "var(--text-secondary)",
  fontSize: 13,
  margin: 0,
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

function rowStyle(level: FleetAlert["level"]): CSSProperties {
  return {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    padding: "10px 12px",
    borderRadius: 8,
    border: "1px solid var(--border)",
    borderLeft: `3px solid ${LEVEL_COLOR[level]}`,
    background: "var(--bg-tertiary)",
  };
}

function chipStyle(level: FleetAlert["level"]): CSSProperties {
  return {
    flexShrink: 0,
    alignSelf: "center",
    background: LEVEL_COLOR[level],
    color: "var(--text-on-accent, #fff)",
    borderRadius: 9999,
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    padding: "2px 8px",
  };
}

const navLinkStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--text-primary)",
  cursor: "pointer",
  font: "inherit",
  fontWeight: 600,
  padding: 0,
  textAlign: "left",
  textDecoration: "underline",
  textDecorationStyle: "dotted",
};
