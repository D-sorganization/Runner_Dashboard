/**
 * AlertsCenter — consolidated fleet-alert surface (issue #819).
 *
 * Replaces the old behaviour where alerts dominated the top-middle of the
 * screen and re-popped on every poll. Renders TWO things:
 *
 *   1. A compact **status pill** in the shell topbar. Colour reflects the
 *      worst *unacknowledged* severity (red = critical, yellow = warning,
 *      green = ok). The pill is never modal — clicking it opens the drawer.
 *   2. A dismissible **alerts drawer** anchored to the right edge. Each row can
 *      be acknowledged or snoozed; suppressions are durable via lib/alertAck.
 *
 * Critical alerts keep the pill red but never block the UI. Acknowledged rows
 * collapse into an "N acknowledged" affordance the operator can expand.
 *
 * This component owns only presentation + the ack/snooze wiring. The alert data
 * itself is computed by the pure `computeFleetAlerts` (lib/fleetAlerts.ts) and
 * passed in, so the surface stays orthogonal to the polling layer.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import type { FleetAlert, FleetLevel } from "../lib/fleetAlerts";
import { fleetLevelLabel } from "../lib/fleetAlerts";
import {
  ack,
  clear,
  isAcked,
  snooze,
  SNOOZE_DURATIONS_MS,
} from "../lib/alertAck";

export interface AlertsCenterProps {
  /** Live, recomputed-every-poll alert list from computeFleetAlerts. */
  alerts: ReadonlyArray<FleetAlert>;
  /**
   * Optional navigation callback so a row can deep-link to the relevant tab.
   * Receives the alert id; the host maps it to a tab.
   */
  onNavigate?: (alertId: FleetAlert["id"]) => void;
  /** Injectable clock for deterministic tests. */
  now?: () => number;
}

const LEVEL_COLOR: Record<FleetLevel, string> = {
  ok: "var(--accent-green)",
  warning: "var(--accent-yellow)",
  critical: "var(--accent-red)",
};

const ALERT_LEVEL_RANK: Record<FleetAlert["level"], number> = {
  warning: 1,
  critical: 2,
};

/**
 * Worst severity across the *visible* (unacknowledged) alerts. Acked alerts do
 * not raise the pill colour — that is the whole point of the durable ack.
 */
function visibleLevel(visible: ReadonlyArray<FleetAlert>): FleetLevel {
  let rank = 0;
  for (const a of visible) {
    rank = Math.max(rank, ALERT_LEVEL_RANK[a.level]);
  }
  if (rank >= 2) return "critical";
  if (rank >= 1) return "warning";
  return "ok";
}

export function AlertsCenter({ alerts, onNavigate, now = Date.now }: AlertsCenterProps) {
  const [open, setOpen] = useState(false);
  const [showAcked, setShowAcked] = useState(false);
  // Bumped to force a re-evaluation of isAcked after an ack/snooze/clear (the
  // ack state lives in localStorage, not React, so we need an explicit nudge).
  const [revision, setRevision] = useState(0);

  const partitioned = useMemo(() => {
    void revision; // re-evaluate when ack state changes
    const visible: FleetAlert[] = [];
    const acked: FleetAlert[] = [];
    const t = now();
    for (const a of alerts) {
      if (isAcked(a.id, a.contentHash, t)) acked.push(a);
      else visible.push(a);
    }
    return { visible, acked };
  }, [alerts, revision, now]);

  const level = visibleLevel(partitioned.visible);
  const visibleCount = partitioned.visible.length;
  const ackedCount = partitioned.acked.length;

  const bump = useCallback(() => setRevision((r) => r + 1), []);

  const handleAck = useCallback(
    (a: FleetAlert) => {
      ack(a.id, a.contentHash, now());
      bump();
    },
    [bump, now],
  );

  const handleSnooze = useCallback(
    (a: FleetAlert, durationMs: number) => {
      snooze(a.id, a.contentHash, durationMs, now());
      bump();
    },
    [bump, now],
  );

  const handleUnack = useCallback(
    (a: FleetAlert) => {
      clear(a.id);
      bump();
    },
    [bump],
  );

  // Escape closes the drawer (it is non-modal, so this is a convenience only).
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const pillLabel =
    visibleCount === 0
      ? "All clear"
      : `${visibleCount} alert${visibleCount === 1 ? "" : "s"}`;

  return (
    <>
      <button
        type="button"
        className={`alerts-pill alerts-pill--${level}`}
        data-touch-primitive="AlertsCenter"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Fleet ${fleetLevelLabel(level)} — ${pillLabel}. Open alerts.`}
        onClick={() => setOpen((o) => !o)}
        style={pillStyle(level)}
      >
        <span
          aria-hidden="true"
          className="alerts-pill__dot"
          style={{ ...dotStyle, background: LEVEL_COLOR[level] }}
        />
        <span className="alerts-pill__label">{pillLabel}</span>
      </button>

      {open ? (
        <div
          className="alerts-drawer"
          role="dialog"
          aria-label="Fleet alerts"
          aria-modal="false"
          style={drawerStyle}
        >
          <div className="alerts-drawer__header" style={drawerHeaderStyle}>
            <strong style={{ fontSize: 14 }}>
              Fleet alerts ({visibleCount})
            </strong>
            <button
              type="button"
              aria-label="Close alerts"
              className="alerts-drawer__close"
              onClick={() => setOpen(false)}
              style={closeBtnStyle}
            >
              ×
            </button>
          </div>

          {visibleCount === 0 ? (
            <p className="alerts-drawer__empty" style={emptyStyle}>
              No active alerts. All systems nominal.
            </p>
          ) : (
            <ul className="alerts-drawer__list" style={listStyle}>
              {partitioned.visible.map((a) => (
                <li
                  key={a.id}
                  className={`alerts-drawer__row alerts-drawer__row--${a.level}`}
                  style={rowStyle(a.level)}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      className="alerts-drawer__title"
                      style={{ fontWeight: 600 }}
                    >
                      {onNavigate ? (
                        <button
                          type="button"
                          className="alerts-drawer__navlink"
                          onClick={() => onNavigate(a.id)}
                          style={navLinkStyle}
                        >
                          {a.title}
                        </button>
                      ) : (
                        a.title
                      )}
                    </div>
                    <div
                      className="alerts-drawer__detail"
                      style={{ color: "var(--text-secondary)", fontSize: 12 }}
                    >
                      {a.detail}
                    </div>
                  </div>
                  <div className="alerts-drawer__actions" style={actionsStyle}>
                    <button
                      type="button"
                      className="alerts-drawer__ack"
                      onClick={() => handleAck(a)}
                      style={smallBtnStyle}
                    >
                      Acknowledge
                    </button>
                    <button
                      type="button"
                      className="alerts-drawer__snooze"
                      title="Snooze for 1 hour"
                      onClick={() => handleSnooze(a, SNOOZE_DURATIONS_MS.oneHour)}
                      style={smallBtnStyle}
                    >
                      Snooze 1h
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {ackedCount > 0 ? (
            <div className="alerts-drawer__acked" style={ackedSectionStyle}>
              <button
                type="button"
                className="alerts-drawer__acked-toggle"
                aria-expanded={showAcked}
                onClick={() => setShowAcked((s) => !s)}
                style={ackedToggleStyle}
              >
                {showAcked ? "▾" : "▸"} {ackedCount} acknowledged
              </button>
              {showAcked ? (
                <ul style={listStyle}>
                  {partitioned.acked.map((a) => (
                    <li key={a.id} style={ackedRowStyle}>
                      <span style={{ flex: 1, minWidth: 0 }}>{a.title}</span>
                      <button
                        type="button"
                        className="alerts-drawer__unack"
                        onClick={() => handleUnack(a)}
                        style={smallBtnStyle}
                      >
                        Un-acknowledge
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

function pillStyle(level: FleetLevel): CSSProperties {
  return {
    alignItems: "center",
    background: "var(--bg-tertiary)",
    border: `1px solid ${level === "ok" ? "transparent" : LEVEL_COLOR[level]}`,
    borderRadius: "9999px",
    color: "var(--text-secondary)",
    cursor: "pointer",
    display: "inline-flex",
    fontSize: 12,
    fontWeight: 600,
    gap: 6,
    minHeight: 30,
    padding: "4px 12px",
    touchAction: "manipulation",
    whiteSpace: "nowrap",
  };
}

const dotStyle: CSSProperties = {
  borderRadius: "50%",
  display: "inline-block",
  height: 8,
  width: 8,
};

const drawerStyle: CSSProperties = {
  position: "fixed",
  top: 64,
  right: 16,
  width: "min(380px, calc(100vw - 32px))",
  maxHeight: "calc(100vh - 96px)",
  overflowY: "auto",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  boxShadow: "var(--glass-shadow, 0 8px 32px 0 rgba(0,0,0,0.37))",
  zIndex: 9999,
  padding: 12,
};

const drawerHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginBottom: 8,
};

const closeBtnStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--text-secondary)",
  cursor: "pointer",
  fontSize: 20,
  lineHeight: 1,
  minHeight: 32,
  minWidth: 32,
};

const emptyStyle: CSSProperties = {
  color: "var(--text-secondary)",
  fontSize: 13,
  margin: "8px 4px",
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
    gap: 10,
    alignItems: "flex-start",
    padding: "10px 12px",
    borderRadius: 8,
    border: "1px solid var(--border)",
    borderLeft: `3px solid ${LEVEL_COLOR[level]}`,
    background: "var(--bg-tertiary)",
  };
}

const actionsStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  flexShrink: 0,
};

const smallBtnStyle: CSSProperties = {
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 6,
  color: "var(--text-primary)",
  cursor: "pointer",
  fontSize: 12,
  fontWeight: 600,
  minHeight: 30,
  padding: "4px 10px",
  whiteSpace: "nowrap",
};

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

const ackedSectionStyle: CSSProperties = {
  marginTop: 10,
  borderTop: "1px solid var(--border)",
  paddingTop: 8,
};

const ackedToggleStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--text-secondary)",
  cursor: "pointer",
  fontSize: 12,
  fontWeight: 600,
  padding: "4px 0",
};

const ackedRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 12,
  color: "var(--text-secondary)",
  padding: "4px 0",
};
