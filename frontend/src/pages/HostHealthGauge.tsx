import React from "react";
import { Badge } from "../primitives/Badge";

export interface HostHealthGaugeData {
  level?: "green" | "amber" | "red";
  desired?: number;
  online?: number;
  busy?: number;
  cap?: number;
  schedule_window?: { name?: string; start?: string; end?: string; runners?: number };
  target_override?: { runners?: number; until?: string | null; reason?: string };
  suggested_action?: string | null;
}

const TONE: Record<string, "success" | "warning" | "danger"> = {
  green: "success",
  amber: "warning",
  red: "danger",
};

export function HostHealthGauge({
  health,
}: {
  health?: HostHealthGaugeData | null;
}): React.ReactElement | null {
  if (!health || !health.level) return null;
  const tone = TONE[health.level] || "neutral";
  const window = health.schedule_window;
  const override = health.target_override;
  return (
    <div className="host-health-gauge" style={{ marginTop: 8, marginBottom: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Badge tone={tone} size="sm">
          Health: {health.level}
        </Badge>
        <span className="stat-sub">
          desired {health.desired ?? "-"} / online {health.online ?? "-"} / busy{" "}
          {health.busy ?? "-"} / cap {health.cap ?? "-"}
        </span>
      </div>
      {window ? (
        <div className="stat-sub" style={{ marginTop: 4 }}>
          Schedule: {window.name || "active"} {window.start}-{window.end} → {window.runners}{" "}
          runners
        </div>
      ) : null}
      {override ? (
        <div className="stat-sub" style={{ marginTop: 4 }}>
          Override: {override.runners} runners
          {override.until ? ` until ${override.until}` : ""}
        </div>
      ) : null}
      {health.suggested_action ? (
        <div
          className="stat-sub"
          style={{ marginTop: 4, color: "var(--accent-orange)" }}
          role="status"
        >
          Suggested: {health.suggested_action}
        </div>
      ) : null}
    </div>
  );
}
