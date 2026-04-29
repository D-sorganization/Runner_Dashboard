import type { ReactNode } from "react";

export interface KpiHeaderProps {
  total: number;
  online: number;
  busy: number;
  offline: number;
}

function KpiValue({ label, value }: { label: string; value: number }) {
  return (
    <div className="kpi-item" style={{ textAlign: "center", flex: "1 1 0" }}>
      <div
        className="kpi-value"
        style={{
          fontSize: "20px",
          fontWeight: 700,
          lineHeight: "1.2",
          marginBottom: "2px",
        }}
      >
        {value}
      </div>
      <div
        className="kpi-label"
        style={{
          color: "var(--text-secondary)",
          fontSize: "11px",
          fontWeight: 500,
          letterSpacing: "0.05em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
    </div>
  );
}

export function KpiHeader({ total, online, busy, offline }: KpiHeaderProps) {
  return (
    <div
      className="kpi-header"
      role="region"
      aria-label="Fleet summary"
      style={{
        display: "flex",
        gap: "8px",
        justifyContent: "space-around",
        padding: "12px 8px",
      }}
    >
      <KpiValue label="Total" value={total} />
      <KpiValue label="Online" value={online} />
      <KpiValue label="Busy" value={busy} />
      <KpiValue label="Offline" value={offline} />
    </div>
  );
}
