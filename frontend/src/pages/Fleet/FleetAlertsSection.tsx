import React from "react";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";

export interface FleetAlertItem {
  id: string;
  level: "warning" | "critical" | "info" | "ok" | string;
  title: string;
  detail?: string;
  contentHash?: string;
}

export interface RunnerAuditViolation {
  repo?: string;
  workflow?: string;
  job_name?: string;
  runner_name?: string;
  runner_group?: string;
  started_at?: string | null;
  run_url?: string | null;
}

export interface RunnerAuditData {
  violations?: RunnerAuditViolation[];
  last_checked?: string | null;
  error?: string | null;
}

export interface FleetAlertsSectionProps {
  alerts?: FleetAlertItem[];
  runnerAudit?: RunnerAuditData | null;
  loading?: boolean;
  error?: string | null;
  onRefreshAudit?: () => void;
  onRetry?: () => void;
}

export function FleetAlertsSection({
  alerts = [],
  runnerAudit = null,
  loading = false,
  error = null,
  onRefreshAudit,
  onRetry,
}: FleetAlertsSectionProps): React.ReactElement {
  const violations = runnerAudit?.violations ?? [];
  const hasAlerts = alerts.length > 0;
  const hasViolations = violations.length > 0;

  return (
    <section
      id="alerts"
      className="fleet-section fleet-alerts-section"
      aria-label="Fleet Alerts & Billing Audit"
      style={{
        marginBottom: "1.5rem",
        border: "1px solid var(--border-subtle, rgba(255, 255, 255, 0.1))",
        borderRadius: "8px",
        padding: "1rem",
        backgroundColor: "var(--bg-secondary, #161b22)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.5rem",
          marginBottom: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h2 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 600 }}>
            Fleet Alerts & Billing Audit
          </h2>
          {hasViolations ? (
            <Badge tone="danger" size="sm">
              {violations.length} violation{violations.length === 1 ? "" : "s"}
            </Badge>
          ) : hasAlerts ? (
            <Badge tone="warning" size="sm">
              {alerts.length} alert{alerts.length === 1 ? "" : "s"}
            </Badge>
          ) : (
            <Badge tone="success" size="sm">
              Nominal
            </Badge>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {runnerAudit?.last_checked ? (
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted, #8b949e)" }}>
              {"Last checked: " + new Date(runnerAudit.last_checked).toLocaleTimeString()}
            </span>
          ) : null}
          {onRefreshAudit ? (
            <TouchButton
              type="button"
              onClick={onRefreshAudit}
              disabled={loading}
              aria-label="Refresh Audit"
              style={{
                fontSize: "0.85rem",
                padding: "0.25rem 0.6rem",
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              Refresh Audit
            </TouchButton>
          ) : null}
        </div>
      </div>

      {error ? (
        <div
          role="alert"
          style={{
            padding: "0.75rem",
            marginBottom: "1rem",
            borderRadius: "6px",
            backgroundColor: "var(--bg-danger-subtle, rgba(248, 81, 73, 0.15))",
            border: "1px solid var(--border-danger, #f85149)",
            color: "var(--text-danger, #f85149)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.5rem",
          }}
        >
          <span>{error}</span>
          {onRetry ? (
            <TouchButton
              type="button"
              onClick={onRetry}
              aria-label="Retry"
              style={{
                fontSize: "0.8rem",
                padding: "0.2rem 0.5rem",
                cursor: "pointer",
              }}
            >
              Retry
            </TouchButton>
          ) : null}
        </div>
      ) : null}

      {!error && !hasAlerts && !hasViolations ? (
        <div
          style={{
            padding: "1.5rem",
            textAlign: "center",
            color: "var(--text-muted, #8b949e)",
            fontSize: "0.9rem",
          }}
        >
          No active alerts or hosted-runner violations detected. All systems nominal.
        </div>
      ) : null}

      {hasAlerts ? (
        <div style={{ marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "1rem", margin: "0.5rem 0", color: "var(--text-primary, #c9d1d9)" }}>
            Active Fleet Alerts
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {alerts.map((alert) => (
              <div
                key={alert.id}
                style={{
                  padding: "0.6rem 0.8rem",
                  borderRadius: "6px",
                  backgroundColor: "var(--bg-tertiary, #21262d)",
                  border:
                    alert.level === "critical"
                      ? "1px solid var(--border-danger, #f85149)"
                      : "1px solid var(--border-warning, #d29922)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.25rem",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <Badge
                    tone={alert.level === "critical" ? "danger" : "warning"}
                    size="sm"
                  >
                    {alert.level}
                  </Badge>
                  <strong style={{ fontSize: "0.9rem" }}>{alert.title}</strong>
                </div>
                {alert.detail ? (
                  <p
                    style={{
                      margin: 0,
                      fontSize: "0.85rem",
                      color: "var(--text-muted, #8b949e)",
                    }}
                  >
                    {alert.detail}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {hasViolations ? (
        <div>
          <div
            style={{
              padding: "0.6rem 0.8rem",
              marginBottom: "0.75rem",
              borderRadius: "6px",
              backgroundColor: "var(--bg-warning-subtle, rgba(210, 153, 34, 0.15))",
              border: "1px solid var(--border-warning, #d29922)",
              fontSize: "0.85rem",
              color: "var(--text-warning, #d29922)",
            }}
          >
            <strong>{violations.length} hosted runner violation(s) found.</strong> These jobs ran
            on GitHub-hosted runners and may incur billing costs.
          </div>
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "0.85rem",
              }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid var(--border-subtle, rgba(255, 255, 255, 0.1))",
                    textAlign: "left",
                  }}
                >
                  <th style={{ padding: "0.5rem" }}>Repo</th>
                  <th style={{ padding: "0.5rem" }}>Workflow</th>
                  <th style={{ padding: "0.5rem" }}>Job</th>
                  <th style={{ padding: "0.5rem" }}>Runner</th>
                  <th style={{ padding: "0.5rem" }}>Started</th>
                  <th style={{ padding: "0.5rem" }}>Link</th>
                </tr>
              </thead>
              <tbody>
                {violations.map((v, i) => (
                  <tr
                    key={i}
                    style={{
                      borderBottom: "1px solid var(--border-subtle, rgba(255, 255, 255, 0.05))",
                    }}
                  >
                    <td style={{ padding: "0.5rem", fontWeight: 500 }}>{v.repo}</td>
                    <td style={{ padding: "0.5rem", color: "var(--text-muted, #8b949e)" }}>
                      {v.workflow}
                    </td>
                    <td style={{ padding: "0.5rem", color: "var(--text-muted, #8b949e)" }}>
                      {v.job_name}
                    </td>
                    <td style={{ padding: "0.5rem" }}>
                      <Badge tone="danger" size="sm">
                        {v.runner_name || v.runner_group || "unknown"}
                      </Badge>
                    </td>
                    <td style={{ padding: "0.5rem", color: "var(--text-muted, #8b949e)" }}>
                      {v.started_at ? new Date(v.started_at).toLocaleString() : "—"}
                    </td>
                    <td style={{ padding: "0.5rem" }}>
                      {v.run_url ? (
                        <a
                          href={v.run_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{
                            color: "var(--accent-blue, #58a6ff)",
                            textDecoration: "none",
                          }}
                        >
                          View Run ↗
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default FleetAlertsSection;
