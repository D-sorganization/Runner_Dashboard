import type { ReactNode } from "react";

export type RunnerStatus = "online" | "busy" | "offline";

export interface StatusPillProps {
  count: number;
  label: string;
  onClick?: () => void;
  selected?: boolean;
  status: RunnerStatus;
}

const statusColors: Record<RunnerStatus, { bg: string; text: string }> = {
  online: { bg: "rgba(63,185,80,0.15)", text: "#3fb950" },
  busy: { bg: "rgba(210,153,34,0.15)", text: "#d29922" },
  offline: { bg: "rgba(248,81,73,0.15)", text: "#f85149" },
};

export function StatusPill({ count, label, onClick, selected, status }: StatusPillProps) {
  const colors = statusColors[status];
  return (
    <button
      aria-pressed={selected}
      className={`status-pill ${selected ? "status-pill-active" : ""}`}
      onClick={onClick}
      style={{
        background: colors.bg,
        border: `1px solid ${selected ? colors.text : "transparent"}`,
        borderRadius: "9999px",
        color: colors.text,
        cursor: onClick ? "pointer" : "default",
        fontSize: "12px",
        fontWeight: 600,
        minHeight: "32px",
        padding: "6px 12px",
        textAlign: "center",
        touchAction: "manipulation",
        userSelect: "none",
        whiteSpace: "nowrap",
      }}
      type="button"
    >
      <span aria-label={`${count} ${label}`} className="status-pill-count">
        {count}
      </span>
      <span className="status-pill-label" style={{ marginLeft: "4px", opacity: 0.85 }}>
        {label}
      </span>
    </button>
  );
}
