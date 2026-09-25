import React from "react";
import { ServerGlyph } from "../decompIcons";

export interface FleetStatusBannerProps {
  loading: boolean;
  runnersLoaded: boolean;
  nodesLoaded: boolean;
  failedSources?: string[];
  isStale?: boolean;
  error?: string | null;
  runners?: Array<{ id: number; name: string; status: string; busy: boolean; [key: string]: unknown }>;
  stats?: Record<string, unknown>;
  driftInfo?: { is_drifted?: boolean; summary?: string } | null;
  deployment?: Record<string, unknown>;
  onRetry?: () => void;
  onOpenDeployment?: () => void;
}

export const FleetStatusBanner: React.FC<FleetStatusBannerProps> = ({
  loading,
  runnersLoaded,
  nodesLoaded,
  failedSources = [],
  isStale = false,
  error = null,
  runners = [],
  stats = {},
  driftInfo = null,
  onRetry,
  onOpenDeployment,
}) => {
  const isReady = !loading && runnersLoaded && nodesLoaded && failedSources.length === 0 && !error;

  const onlineCount = runners.filter((r) => r.status === "online").length;
  const busyCount = runners.filter((r) => r.busy).length;
  const offlineCount = runners.filter((r) => r.status !== "online").length;
  const queuedCount = Number(stats.queued || stats.queued_count || 0);
  const successRate = stats.success_rate !== undefined ? `${stats.success_rate}%` : "-";

  let statusTitle = "Fleet Operational";
  let statusSummary = "All systems nominal";
  let statusClass = "status-banner--nominal";
  let statusBg = "rgba(46, 160, 67, 0.15)";
  let statusBorder = "var(--border-green, #2ea043)";
  let statusText = "var(--accent-green, #3fb950)";

  if (failedSources.length > 0 || error) {
    statusTitle = "Fleet Unknown";
    const failDetail = failedSources.length > 0 ? failedSources.join("; ") : (error || "telemetry failure");
    statusSummary = `Fleet status unknown — ${failDetail}`;
    statusClass = "status-banner--failing";
    statusBg = "rgba(248, 81, 73, 0.15)";
    statusBorder = "var(--border-red, #da3633)";
    statusText = "var(--accent-red, #f85149)";
  } else if (!runnersLoaded || !nodesLoaded || loading) {
    statusTitle = "Fleet Unknown";
    statusSummary = "Checking fleet…";
    statusClass = "status-banner--unknown";
    statusBg = "rgba(110, 118, 129, 0.15)";
    statusBorder = "var(--border-muted, #6e7681)";
    statusText = "var(--text-muted, #8b949e)";
  } else if (isStale) {
    statusTitle = "Fleet Stale";
    statusSummary = "Telemetry stale (> 60s without successful update)";
    statusClass = "status-banner--stale";
    statusBg = "rgba(210, 153, 34, 0.15)";
    statusBorder = "var(--border-yellow, #bb8009)";
    statusText = "var(--accent-yellow, #d29922)";
  } else if (offlineCount > 0) {
    statusTitle = "Fleet Degraded";
    statusSummary = `${offlineCount} runner${offlineCount > 1 ? "s" : ""} offline`;
    statusClass = "status-banner--degraded";
    statusBg = "rgba(210, 153, 34, 0.15)";
    statusBorder = "var(--border-yellow, #bb8009)";
    statusText = "var(--accent-yellow, #d29922)";
  }

  return (
    <div
      role="region"
      aria-label="Fleet status"
      className={`fleet-status-banner ${statusClass}`}
      style={{
        border: `1px solid ${statusBorder}`,
        background: statusBg,
        borderRadius: 8,
        padding: "16px 20px",
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <ServerGlyph size={24} />
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: statusText }}>
              {statusTitle}
            </div>
            <div style={{ fontSize: 13, color: "var(--text-secondary, #c9d1d9)", marginTop: 2 }}>
              {statusSummary}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {driftInfo?.is_drifted && (
            <button
              type="button"
              onClick={onOpenDeployment}
              className="btn btn--warning"
              style={{ fontSize: 12, padding: "4px 8px" }}
            >
              ⚠ Deployment drift detected
            </button>
          )}
          {onOpenDeployment && (
            <button
              type="button"
              onClick={onOpenDeployment}
              className="btn"
              style={{ fontSize: 12, padding: "4px 8px" }}
            >
              Deployment state
            </button>
          )}
          {onRetry && (failedSources.length > 0 || error || isStale) && (
            <button
              type="button"
              onClick={onRetry}
              className="btn"
              style={{ fontSize: 12, padding: "4px 10px" }}
            >
              Retry Check
            </button>
          )}
        </div>
      </div>

      {/* KPI Stats Strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
          gap: 12,
          marginTop: 16,
          paddingTop: 12,
          borderTop: "1px solid var(--border, #30363d)",
        }}
      >
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)", textTransform: "uppercase", fontWeight: 600 }}>
            Online
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: "var(--accent-green, #3fb950)" }}>
            {isReady ? onlineCount : "-"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)", textTransform: "uppercase", fontWeight: 600 }}>
            Busy
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: "var(--accent-blue, #58a6ff)" }}>
            {isReady ? busyCount : "-"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)", textTransform: "uppercase", fontWeight: 600 }}>
            Offline
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: offlineCount > 0 ? "var(--accent-red, #f85149)" : "var(--text-secondary, #c9d1d9)" }}>
            {isReady ? offlineCount : "-"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)", textTransform: "uppercase", fontWeight: 600 }}>
            Queued
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary, #c9d1d9)" }}>
            {isReady ? queuedCount : "-"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted, #8b949e)", textTransform: "uppercase", fontWeight: 600 }}>
            Success Rate
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary, #c9d1d9)" }}>
            {isReady ? successRate : "-"}
          </div>
        </div>
      </div>

      {/* Section Quick-Jump Nav Links */}
      <div
        style={{
          display: "flex",
          gap: 12,
          marginTop: 12,
          paddingTop: 8,
          borderTop: "1px solid var(--border-subtle, rgba(240, 246, 252, 0.1))",
          fontSize: 12,
        }}
      >
        <span style={{ color: "var(--text-muted, #8b949e)", fontWeight: 500 }}>Jump to:</span>
        <a href="#machines" style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "none", fontWeight: 500 }}>
          Machines
        </a>
        <a href="#runners" style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "none", fontWeight: 500 }}>
          Runners
        </a>
        <a href="#alerts" style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "none", fontWeight: 500 }}>
          Alerts & Audit
        </a>
        <a href="#events" style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "none", fontWeight: 500 }}>
          Event Log
        </a>
      </div>
    </div>
  );
};
