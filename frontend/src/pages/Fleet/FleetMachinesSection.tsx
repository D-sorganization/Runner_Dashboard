import React, { useState } from "react";
import { formatBytes } from "../../components/formatters";

export interface StorageDevice {
  device?: string;
  mountpoint?: string;
  percent?: number;
  total_bytes?: number;
  used_bytes?: number;
}

export interface MachineNode {
  name: string;
  online?: boolean;
  dashboard_reachable?: boolean;
  role?: string;
  offline_reason?: string;
  offline_detail?: string;
  last_seen?: string | null;
  health?: {
    runners_registered?: number;
  };
  system?: {
    cpu?: {
      percent?: number;
      percent_1m_avg?: number;
      count?: number;
      load_avg?: number[];
    };
    memory?: {
      percent?: number;
      used?: number;
      total?: number;
    };
    swap?: {
      percent?: number;
    };
    storage?: StorageDevice[];
    os?: {
      wsl_distro?: string;
      kernel?: string;
    };
  };
}

export interface FleetMachinesSectionProps {
  machines?: MachineNode[];
  nodes?: MachineNode[];
  runners?: Array<{ id: number; name: string; status: string; busy: boolean; labels?: Array<{ name?: string } | string> }>;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onAskMaintenance?: (machineName: string, actionPrompt: string) => void;
  onControlAction?: (action: string) => void;
}

export const FleetMachinesSection: React.FC<FleetMachinesSectionProps> = ({
  machines,
  nodes,
  runners = [],
  loading = false,
  error = null,
  onRetry,
  onAskMaintenance,
  onControlAction: _onControlAction,
}) => {
  const machineList = machines || nodes || [];
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleExpand = (name: string) => {
    setExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const runnersByMachine: Record<string, typeof runners> = {};
  runners.forEach((r) => {
    const parts = r.name.split("-");
    const mName = parts.length >= 4 ? parts[2] : (parts[0] || "Unknown");
    if (!runnersByMachine[mName]) runnersByMachine[mName] = [];
    runnersByMachine[mName].push(r);
  });

  return (
    <div
      id="machines"
      role="region"
      aria-label="Fleet Machines"
      className="section section--stacked fleet-machines-section"
      style={{
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        background: "var(--bg-secondary, #161b22)",
        padding: "16px 20px",
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary, #c9d1d9)", margin: 0 }}>
            Fleet Machines ({machineList.length})
          </h2>
          <span style={{ fontSize: 12, color: "var(--text-muted, #8b949e)" }}>
            Physical nodes and runner host telemetry
          </span>
        </div>
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
          <span>Failed to load machines: {error}</span>
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

      {loading && machineList.length === 0 ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted, #8b949e)", fontSize: 13 }}>
          Loading machine telemetry…
        </div>
      ) : machineList.length === 0 && !error ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted, #8b949e)", fontSize: 13 }}>
          No machines registered in the fleet.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border, #30363d)", color: "var(--text-muted, #8b949e)" }}>
                <th style={{ padding: "8px 6px", width: 32 }}></th>
                <th style={{ padding: "8px 10px" }}>Machine</th>
                <th style={{ padding: "8px 10px" }}>Reachability</th>
                <th style={{ padding: "8px 10px" }}>Runners</th>
                <th style={{ padding: "8px 10px" }}>Resources (CPU / RAM)</th>
                <th style={{ padding: "8px 10px" }}>Last Seen</th>
                <th style={{ padding: "8px 10px", textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {machineList.map((m) => {
                const isOnline = m.online !== false;
                const cpuVal = m.system?.cpu?.percent_1m_avg ?? m.system?.cpu?.percent ?? 0;
                const memVal = m.system?.memory?.percent ?? 0;
                const isExp = Boolean(expanded[m.name]);
                const mRunners = runnersByMachine[m.name] || [];
                const onlineRunners = mRunners.filter((r) => r.status === "online").length;

                return (
                  <React.Fragment key={m.name}>
                    <tr style={{ borderBottom: "1px solid var(--border-subtle, rgba(240, 246, 252, 0.08))" }}>
                      <td style={{ padding: "8px 6px" }}>
                        <button
                          type="button"
                          aria-label={`expand ${m.name} telemetry`}
                          onClick={() => toggleExpand(m.name)}
                          style={{
                            background: "transparent",
                            border: "none",
                            color: "var(--text-muted, #8b949e)",
                            cursor: "pointer",
                            fontSize: 12,
                            padding: "2px 4px",
                          }}
                        >
                          {isExp ? "▼" : "▶"}
                        </button>
                      </td>
                      <td style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
                        {m.name}
                        {m.system?.os?.wsl_distro && (
                          <span style={{ fontSize: 10, color: "var(--text-muted, #8b949e)", marginLeft: 6, fontWeight: 400 }}>
                            ({m.system.os.wsl_distro})
                          </span>
                        )}
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        <span
                          style={{
                            display: "inline-block",
                            padding: "2px 6px",
                            borderRadius: 4,
                            fontSize: 11,
                            fontWeight: 600,
                            background: isOnline ? "rgba(46, 160, 67, 0.15)" : "rgba(248, 81, 73, 0.15)",
                            color: isOnline ? "var(--accent-green, #3fb950)" : "var(--accent-red, #f85149)",
                            border: `1px solid ${isOnline ? "var(--border-green, #2ea043)" : "var(--border-red, #da3633)"}`,
                          }}
                        >
                          {isOnline ? "Online" : "Offline"}
                        </span>
                      </td>
                      <td style={{ padding: "8px 10px", color: "var(--text-secondary, #c9d1d9)" }}>
                        {onlineRunners} / {mRunners.length || m.health?.runners_registered || 0}
                      </td>
                      <td style={{ padding: "8px 10px", color: "var(--text-secondary, #c9d1d9)" }}>
                        {isOnline ? (
                          <span>
                            CPU {Math.round(cpuVal)}% · RAM {Math.round(memVal)}%
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-muted, #8b949e)" }}>{m.offline_reason || "unreachable"}</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 10px", color: "var(--text-muted, #8b949e)", fontSize: 11 }}>
                        {m.last_seen ? new Date(m.last_seen).toLocaleTimeString() : "-"}
                      </td>
                      <td style={{ padding: "8px 10px", textAlign: "right" }}>
                        <div style={{ display: "inline-flex", gap: 6 }}>
                          <button
                            type="button"
                            onClick={() => onAskMaintenance?.(m.name, `Inspect and maintain machine ${m.name}`)}
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
                        </div>
                      </td>
                    </tr>
                    {isExp && (
                      <tr style={{ background: "rgba(0, 0, 0, 0.2)" }}>
                        <td colSpan={7} style={{ padding: "10px 16px" }}>
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
                            <div>
                              <div style={{ fontWeight: 600, color: "var(--text-muted, #8b949e)", marginBottom: 4 }}>
                                OS & System Details
                              </div>
                              <div>WSL Distro: {m.system?.os?.wsl_distro || "Linux"}</div>
                              <div>CPU Cores: {m.system?.cpu?.count || "-"}</div>
                              {m.system?.memory?.total && (
                                <div>
                                  Memory: {formatBytes(m.system.memory.used || 0)} / {formatBytes(m.system.memory.total)}
                                </div>
                              )}
                            </div>
                            {m.system?.storage && m.system.storage.length > 0 && (
                              <div>
                                <div style={{ fontWeight: 600, color: "var(--text-muted, #8b949e)", marginBottom: 4 }}>
                                  Storage Devices
                                </div>
                                {m.system.storage.map((d, i) => (
                                  <div key={i} style={{ fontSize: 11 }}>
                                    {d.device || d.mountpoint}: {Math.round(d.percent || 0)}%
                                    {d.total_bytes && ` (${formatBytes(d.used_bytes || 0)} / ${formatBytes(d.total_bytes)})`}
                                  </div>
                                ))}
                              </div>
                            )}
                            <div>
                              <div style={{ fontWeight: 600, color: "var(--text-muted, #8b949e)", marginBottom: 4 }}>
                                Runner Pool ({mRunners.length})
                              </div>
                              {mRunners.length > 0 ? (
                                mRunners.map((r) => (
                                  <div key={r.id} style={{ fontSize: 11, color: "var(--text-secondary, #c9d1d9)" }}>
                                    • {r.name} ({r.busy ? "busy" : r.status})
                                  </div>
                                ))
                              ) : (
                                <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)" }}>No runners bound</div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
