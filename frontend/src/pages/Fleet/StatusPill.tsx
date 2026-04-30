import { Pill } from "../../primitives/Pill";

export type RunnerStatus = "online" | "busy" | "offline";

export interface StatusPillProps {
  count: number;
  label: string;
  onClick?: () => void;
  selected?: boolean;
  status: RunnerStatus;
}

const statusColors: Record<RunnerStatus, { bg: string; text: string }> = {
  online: { bg: "var(--badge-success-bg)", text: "var(--badge-success-fg)" },
  busy: { bg: "var(--badge-warning-bg)", text: "var(--badge-warning-fg)" },
  offline: { bg: "var(--badge-danger-bg)", text: "var(--badge-danger-fg)" },
};

export function StatusPill({ count, label, onClick, selected, status }: StatusPillProps) {
  const colors = statusColors[status];
  return (
    <Pill
      aria-pressed={selected}
      className={`status-pill status-pill-${status}${selected ? " status-pill-active" : ""}`}
      onClick={onClick}
      selected={selected}
      style={{
        background: colors.bg,
        border: `1px solid ${selected ? colors.text : "transparent"}`,
        color: colors.text,
        fontSize: "12px",
        minHeight: "32px",
        padding: "6px 12px",
      }}
    >
      <span aria-label={`${count} ${label}`} className="status-pill-count">
        {count}
      </span>
      <span className="status-pill-label" style={{ marginLeft: "4px", opacity: 0.85 }}>
        {label}
      </span>
    </Pill>
  );
}
