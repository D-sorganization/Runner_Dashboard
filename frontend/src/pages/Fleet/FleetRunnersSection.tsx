import React, { useState } from "react";
import { FLEET_ACTIONS, type FleetRowActionKey } from "./fleetActions";
import { FleetRowActions } from "./FleetRowActions";

export interface RunnerLabel {
  name?: string;
}

export interface RunnerItem {
  id: number;
  name: string;
  status: string;
  busy: boolean;
  labels?: Array<RunnerLabel | string>;
  os?: string;
}

export interface RunItem {
  id: number;
  run_number?: number;
  runner_name?: string;
  head_branch?: string;
  workflow_name?: string;
  html_url?: string;
}

export interface FleetRunnersSectionProps {
  runners: RunnerItem[];
  runs?: RunItem[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onFleet?: (action: string) => void;
  onRunner?: (id: number | string, action: string) => void;
  onAskMaintenance?: (runnerName: string, actionPrompt: string) => void;
  onMaintenanceAction?: (target: string, actionKey: FleetRowActionKey, isMachine: boolean) => void;
}

export const FleetRunnersSection: React.FC<FleetRunnersSectionProps> = ({
  runners,
  runs = [],
  loading = false,
  error = null,
  onRetry,
  onFleet,
  onRunner,
  onAskMaintenance,
  onMaintenanceAction,
}) => {
  const [filter, setFilter] = useState<"all" | "online" | "busy" | "offline">("all");

  const onlineRunners = runners.filter((r) => r.status === "online");
  const busyRunners = runners.filter((r) => r.busy);
  const offlineRunners = runners.filter((r) => r.status !== "online");

  const filteredRunners = runners.filter((r) => {
    if (filter === "online") return r.status === "online";
    if (filter === "busy") return r.busy;
    if (filter === "offline") return r.status !== "online";
    return true;
  });

  const runsByRunner: Record<string, RunItem> = {};
  runs.forEach((run) => {
    if (run.runner_name) runsByRunner[run.runner_name] = run;
  });

  return (
    <div
      id="runners"
      role="region"
      aria-label="Runner Fleet"
      className="section section--stacked fleet-runners-section"
      style={{
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        background: "var(--bg-secondary, #161b22)",
        padding: "16px 20px",
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary, #c9d1d9)", margin: 0 }}>
            Runner Fleet ({runners.length})
          </h2>
          <span style={{ fontSize: 12, color: "var(--text-muted, #8b949e)" }}>
            Individual runner registrations and task workers
          </span>
        </div>

        {/* Fleet-wide controls */}
        {onFleet && (
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => onFleet("all-up")}
              className="btn btn--primary"
              style={{
                fontSize: 12,
                padding: "4px 10px",
                background: "var(--accent-green, #238636)",
                color: "var(--color-fg-on-emphasis, #ffffff)",
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              Start All
            </button>
            <button
              type="button"
              onClick={() => onFleet("all-down")}
              className="btn"
              style={{
                fontSize: 12,
                padding: "4px 10px",
                background: "transparent",
                color: "var(--accent-red, #f85149)",
                border: "1px solid var(--border-red, #da3633)",
                borderRadius: 4,
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              Stop All
            </button>
          </div>
        )}
      </div>

      {error ? (
        <div
          role="alert"
          style={{
            padding: "10px 14px",
            borderRadius: 6,
            background: "rgba(248, 81, 73, 0.15)",
            border: "1px solid var(--border-red, #da3633)",
            color: "var(--accent-red, #f85149)",
            fontSize: 13,
            marginBottom: 12,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span>Failed to load runners: {error}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="btn btn--small"
              style={{
                background: "transparent",
                color: "var(--accent-red, #f85149)",
                border: "1px solid var(--border-red, #da3633)",
              }}
            >
              Retry
            </button>
          )}
        </div>
      ) : null}

      {/* Filter Pills */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={() => setFilter("all")}
          style={{
            padding: "3px 10px",
            borderRadius: 16,
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
            border: "1px solid var(--border, #30363d)",
            background: filter === "all" ? "var(--bg-tertiary, #21262d)" : "transparent",
            color: filter === "all" ? "var(--text-primary, #c9d1d9)" : "var(--text-muted, #8b949e)",
          }}
        >
          All ({runners.length})
        </button>
        <button
          type="button"
          onClick={() => setFilter("online")}
          style={{
            padding: "3px 10px",
            borderRadius: 16,
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
            border: "1px solid var(--border, #30363d)",
            background: filter === "online" ? "rgba(46, 160, 67, 0.15)" : "transparent",
            color: filter === "online" ? "var(--accent-green, #3fb950)" : "var(--text-muted, #8b949e)",
          }}
        >
          Online ({onlineRunners.length})
        </button>
        <button
          type="button"
          onClick={() => setFilter("busy")}
          style={{
            padding: "3px 10px",
            borderRadius: 16,
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
            border: "1px solid var(--border, #30363d)",
            background: filter === "busy" ? "rgba(56, 139, 253, 0.15)" : "transparent",
            color: filter === "busy" ? "var(--accent-blue, #58a6ff)" : "var(--text-muted, #8b949e)",
          }}
        >
          Busy ({busyRunners.length})
        </button>
        <button
          type="button"
          onClick={() => setFilter("offline")}
          style={{
            padding: "3px 10px",
            borderRadius: 16,
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
            border: "1px solid var(--border, #30363d)",
            background: filter === "offline" ? "rgba(248, 81, 73, 0.15)" : "transparent",
            color: filter === "offline" ? "var(--accent-red, #f85149)" : "var(--text-muted, #8b949e)",
          }}
        >
          Offline ({offlineRunners.length})
        </button>
      </div>

      {loading && runners.length === 0 ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted, #8b949e)", fontSize: 13 }}>
          Loading runners…
        </div>
      ) : filteredRunners.length === 0 && !error ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted, #8b949e)", fontSize: 13 }}>
          No runners match the selected filter.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border, #30363d)", color: "var(--text-muted, #8b949e)" }}>
                <th style={{ padding: "8px 10px" }}>Runner</th>
                <th style={{ padding: "8px 10px" }}>Status</th>
                <th style={{ padding: "8px 10px" }}>Labels</th>
                <th style={{ padding: "8px 10px" }}>Current Task</th>
                <th style={{ padding: "8px 10px", textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredRunners.map((r) => {
                const isOnline = r.status === "online";
                const isBusy = r.busy;
                const run = runsByRunner[r.name];
                const labels = (r.labels || []).map((l) => (typeof l === "string" ? l : l.name || "")).filter(Boolean);

                return (
                  <tr key={r.id} style={{ borderBottom: "1px solid var(--border-subtle, rgba(240, 246, 252, 0.08))" }}>
                    <td style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
                      {r.name}
                    </td>
                    <td style={{ padding: "8px 10px" }}>
                      <span
                        style={{
                          display: "inline-block",
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontSize: 11,
                          fontWeight: 600,
                          background: isBusy
                            ? "rgba(56, 139, 253, 0.15)"
                            : isOnline
                            ? "rgba(46, 160, 67, 0.15)"
                            : "rgba(248, 81, 73, 0.15)",
                          color: isBusy
                            ? "var(--accent-blue, #58a6ff)"
                            : isOnline
                            ? "var(--accent-green, #3fb950)"
                            : "var(--accent-red, #f85149)",
                          border: `1px solid ${
                            isBusy
                              ? "var(--border-blue, #1f6feb)"
                              : isOnline
                              ? "var(--border-green, #2ea043)"
                              : "var(--border-red, #da3633)"
                          }`,
                        }}
                      >
                        {isBusy ? "busy" : r.status}
                      </span>
                    </td>
                    <td style={{ padding: "8px 10px", color: "var(--text-secondary, #c9d1d9)" }}>
                      {labels.length > 0 ? (
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {labels.map((lbl, idx) => (
                            <span
                              key={idx}
                              style={{
                                fontSize: 10,
                                padding: "1px 5px",
                                borderRadius: 3,
                                background: "rgba(255, 255, 255, 0.06)",
                                color: "var(--text-muted, #8b949e)",
                              }}
                            >
                              {lbl}
                            </span>
                          ))}
                        </div>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td style={{ padding: "8px 10px", color: "var(--text-secondary, #c9d1d9)" }}>
                      {run ? (
                        <a
                          href={run.html_url || "#"}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "none" }}
                        >
                          Run #{run.run_number || run.id} ({run.workflow_name || "job"})
                        </a>
                      ) : (
                        <span style={{ color: "var(--text-muted, #8b949e)" }}>idle</span>
                      )}
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right" }}>
                      <div style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                        <FleetRowActions
                          target={r.name}
                          isMachine={false}
                          onSelectAction={(actionKey) => {
                            if (onMaintenanceAction) {
                              onMaintenanceAction(r.name, actionKey, false);
                            } else {
                              onAskMaintenance?.(r.name, `${FLEET_ACTIONS[actionKey]?.label || actionKey} runner ${r.name}`);
                            }
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => onAskMaintenance?.(r.name, `Maintain runner ${r.name}`)}
                          className="btn"
                          style={{
                            fontSize: 11,
                            padding: "2px 8px",
                            background: "rgba(56, 139, 253, 0.12)",
                            border: "1px solid var(--border-blue, #1f6feb)",
                            color: "var(--accent-blue, #58a6ff)",
                            cursor: "pointer",
                            borderRadius: 4,
                          }}
                        >
                          Ask Maintenance
                        </button>
                        {onRunner && (
                          <button
                            type="button"
                            onClick={() => onRunner(r.id, isOnline ? "stop" : "start")}
                            className="btn"
                            style={{
                              fontSize: 11,
                              padding: "2px 8px",
                              background: "transparent",
                              border: `1px solid ${isOnline ? "var(--border-red, #da3633)" : "var(--border-green, #2ea043)"}`,
                              color: isOnline ? "var(--accent-red, #f85149)" : "var(--accent-green, #3fb950)",
                              cursor: "pointer",
                              borderRadius: 4,
                            }}
                          >
                            {isOnline ? "Stop" : "Start"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
