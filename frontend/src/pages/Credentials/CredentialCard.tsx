import type { CredentialProbe } from "./mobileTypes";

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "ready"
      ? "var(--accent-green, #22c55e)"
      : status === "missing_key" || status === "not_authed"
        ? "var(--accent-yellow, #eab308)"
        : "var(--accent-red, #ef4444)";

  const label =
    status === "ready"
      ? "Ready"
      : status === "missing_key"
        ? "Missing Key"
        : status === "not_authed"
          ? "Not Authenticated"
          : status === "not_installed"
            ? "Not Installed"
            : status;

  return (
    <span
      style={{
        background: color,
        borderRadius: "4px",
        color: "#fff",
        flexShrink: 0,
        fontSize: "10px",
        fontWeight: 700,
        padding: "2px 6px",
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}

interface CredentialCardProps {
  probe: CredentialProbe;
  onClick: (probe: CredentialProbe) => void;
}

export function CredentialCard({ probe, onClick }: CredentialCardProps) {
  return (
    <button
      aria-label={`Credential: ${probe.label}`}
      className="credential-card glass-card"
      onClick={() => onClick(probe)}
      style={{
        alignItems: "flex-start",
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        marginBottom: "10px",
        padding: "14px 16px",
        textAlign: "left",
        width: "100%",
      }}
      type="button"
    >
      <div
        style={{
          alignItems: "center",
          display: "flex",
          gap: "8px",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <span
          style={{
            color: "var(--text-primary)",
            fontSize: "14px",
            fontWeight: 600,
          }}
        >
          {probe.label}
        </span>
        <StatusBadge status={probe.status} />
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
        {probe.detail}
      </div>
      {probe.config_source && probe.config_source !== "unavailable" && (
        <div style={{ color: "var(--text-muted, #6b7280)", fontSize: "11px" }}>
          Source: {probe.config_source}
        </div>
      )}
    </button>
  );
}
