import type { ReactNode } from "react";

export interface RunnerCardProps {
  cpuPercent: number;
  currentJob?: string | null;
  machine: string;
  name: string;
  onAction?: (action: "drain" | "stop" | "restart") => void;
  ramPercent: number;
  status: "online" | "busy" | "offline";
  uptimeSeconds: number;
}

function fmtUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function MiniBar({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div
      aria-label={`${label}: ${Math.round(value)}%`}
      className="mini-bar"
      role="img"
      style={{ display: "flex", alignItems: "center", gap: "4px", flex: "1" }}
    >
      <span
        className="mini-bar-track"
        style={{
          background: "rgba(255,255,255,0.06)",
          borderRadius: "2px",
          flex: "1",
          height: "4px",
          overflow: "hidden",
        }}
      >
        <span
          className="mini-bar-fill"
          style={{
            background: color,
            borderRadius: "2px",
            display: "block",
            height: "100%",
            transition: "width 300ms ease",
            width: `${Math.min(100, Math.max(0, value))}%`,
          }}
        />
      </span>
      <span className="mini-bar-label" style={{ color: "var(--text-muted)", fontSize: "10px", minWidth: "24px", textAlign: "right" }}>
        {Math.round(value)}%
      </span>
    </div>
  );
}

export function RunnerCard({
  cpuPercent,
  currentJob,
  machine,
  name,
  onAction,
  ramPercent,
  status,
  uptimeSeconds,
}: RunnerCardProps) {
  const statusColor = status === "online" ? "#3fb950" : status === "busy" ? "#d29922" : "#f85149";
  const statusText = status === "online" ? "Online" : status === "busy" ? "Busy" : "Offline";

  return (
    <article
      aria-label={`Runner ${name}, ${statusText}`}
      className="runner-card glass-card"
      style={{
        borderLeft: `3px solid ${statusColor}`,
        marginBottom: "8px",
        padding: "12px",
        position: "relative",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "4px" }}>
        <h3 className="runner-name" style={{ fontSize: "14px", fontWeight: 600 }}>
          {name}
        </h3>
        <span
          className="runner-status-dot"
          style={{
            background: statusColor,
            borderRadius: "50%",
            display: "inline-block",
            height: "8px",
            width: "8px",
          }}
        />
      </div>
      <div className="runner-meta" style={{ color: "var(--text-secondary)", fontSize: "12px", marginBottom: "8px" }}>
        {machine} · {fmtUptime(uptimeSeconds)}
      </div>
      {currentJob && (
        <div className="runner-job" style={{ color: "var(--accent-blue)", fontSize: "12px", marginBottom: "8px" }}>
          {currentJob}
        </div>
      )}
      <div style={{ display: "flex", gap: "8px" }}>
        <MiniBar color="#58a6ff" label="CPU" value={cpuPercent} />
        <MiniBar color="#bc8cff" label="RAM" value={ramPercent} />
      </div>
      {onAction && (
        <div className="runner-actions" style={{ display: "flex", gap: "6px", marginTop: "10px" }}>
          {(["drain", "stop", "restart"] as const).map((action) => (
            <button
              className={`touch-button touch-button-${action === "restart" ? "primary" : "default"}`}
              data-action={action}
              key={action}
              onClick={() => onAction(action)}
              style={{ flex: "1", fontSize: "11px", minHeight: "28px", padding: "4px 0", textTransform: "capitalize" }}
              type="button"
            >
              {action}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}
