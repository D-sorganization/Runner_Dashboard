import React from "react";

export interface OperationsDeploySummary {
  expectedVersion?: string;
  rolloutStatus?: string;
  driftCount?: number;
}

export interface OperationsAdmissionSummary {
  mode?: "running" | "paused" | "draining";
  activeLeases?: number;
  workPlanned?: number;
}

export interface OperationsRunnerHoursSummary {
  desiredRunners?: number;
  onlineRunners?: number;
}

export interface OperationsScheduledWorkflowsSummary {
  totalCount?: number;
}

export interface OperationsDiagnosticsSummary {
  gitDrift?: boolean;
  memoryMb?: number;
}

export interface OperationsStatusBannerProps {
  onJumpToSection?: (sectionId: string) => void;
  deploySummary?: OperationsDeploySummary;
  admissionSummary?: OperationsAdmissionSummary;
  runnerHoursSummary?: OperationsRunnerHoursSummary;
  scheduledWorkflowsSummary?: OperationsScheduledWorkflowsSummary;
  diagnosticsSummary?: OperationsDiagnosticsSummary;
}

export function OperationsStatusBanner({
  onJumpToSection,
  deploySummary,
  admissionSummary,
  runnerHoursSummary,
  scheduledWorkflowsSummary,
  diagnosticsSummary,
}: OperationsStatusBannerProps): React.ReactElement {
  const handleJump = (sectionId: string) => {
    if (onJumpToSection) {
      onJumpToSection(sectionId);
    } else if (typeof window !== "undefined") {
      window.location.hash = `#${sectionId}`;
      const el = document.getElementById(sectionId);
      if (el) el.scrollIntoView({ behavior: "smooth" });
    }
  };

  const modeTone =
    admissionSummary?.mode === "running"
      ? "var(--badge-success-bg, rgba(46,160,67,0.15))"
      : admissionSummary?.mode === "paused"
        ? "var(--badge-warning-bg, rgba(210,153,34,0.15))"
        : "var(--badge-danger-bg, rgba(248,81,73,0.15))";

  const modeColor =
    admissionSummary?.mode === "running"
      ? "var(--badge-success-text, #3fb950)"
      : admissionSummary?.mode === "paused"
        ? "var(--badge-warning-text, #d29922)"
        : "var(--badge-danger-text, #f85149)";

  return (
    <header
      role="banner"
      aria-label="Operations Overview and Navigation"
      style={{
        padding: "1.25rem",
        marginBottom: "1.5rem",
        borderRadius: "8px",
        background: "var(--bg-secondary, #161b22)",
        border: "1px solid var(--border-color, #30363d)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1.25rem",
        }}
      >
        <div>
          <h1
            style={{
              fontSize: "1.5rem",
              fontWeight: 600,
              margin: "0 0 0.35rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Fleet Operations
          </h1>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
              margin: 0,
            }}
          >
            Deployment rollouts, Conductor admission, runner hours, scheduled workflows, and diagnostics
          </p>
        </div>

        {/* Section Jump Links */}
        <nav
          aria-label="Operations sub-sections"
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.5rem",
          }}
        >
          <button
            type="button"
            onClick={() => handleJump("deploy")}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            Deploy & versions
          </button>
          <button
            type="button"
            onClick={() => handleJump("admission")}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            Admission (Conductor)
          </button>
          <button
            type="button"
            onClick={() => handleJump("runner-hours")}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            Runner hours
          </button>
          <button
            type="button"
            onClick={() => handleJump("scheduled-workflows")}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            Scheduled workflows
          </button>
          <button
            type="button"
            onClick={() => handleJump("diagnostics")}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            Diagnostics
          </button>
        </nav>
      </div>

      {/* KPI Summary Strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: "0.75rem",
          marginTop: "0.5rem",
        }}
      >
        {deploySummary && (
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>
              Deploy & versions
            </div>
            <div
              style={{
                fontSize: "1rem",
                fontWeight: 600,
                marginTop: "0.2rem",
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                color: "var(--text-primary, #c9d1d9)",
              }}
            >
              <span>{deploySummary.expectedVersion || "v4.10"}</span>
              {deploySummary.rolloutStatus && (
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 500,
                    padding: "0.1rem 0.4rem",
                    borderRadius: "4px",
                    background: "var(--badge-neutral-bg, rgba(110,118,129,0.2))",
                    color: "var(--badge-neutral-text, #8b949e)",
                  }}
                >
                  {deploySummary.rolloutStatus}
                </span>
              )}
            </div>
          </div>
        )}

        {admissionSummary && (
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>
              Admission Gate
            </div>
            <div
              style={{
                fontSize: "1rem",
                fontWeight: 600,
                marginTop: "0.2rem",
                color: "var(--text-primary, #c9d1d9)",
              }}
            >
              {admissionSummary.mode && (
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    padding: "0.15rem 0.5rem",
                    borderRadius: "4px",
                    background: modeTone,
                    color: modeColor,
                  }}
                >
                  {admissionSummary.mode}
                </span>
              )}
              {admissionSummary.activeLeases !== undefined && (
                <span style={{ fontSize: "0.8125rem", marginLeft: "0.5rem", fontWeight: 400 }}>
                  ({admissionSummary.activeLeases} leases)
                </span>
              )}
            </div>
          </div>
        )}

        {runnerHoursSummary && (
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>
              Runner Hours
            </div>
            <div
              style={{
                fontSize: "1rem",
                fontWeight: 600,
                marginTop: "0.2rem",
                color: "var(--text-primary, #c9d1d9)",
              }}
            >
              {runnerHoursSummary.onlineRunners ?? 0}/{runnerHoursSummary.desiredRunners ?? 0} online
            </div>
          </div>
        )}

        {scheduledWorkflowsSummary && (
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>
              Scheduled Workflows
            </div>
            <div
              style={{
                fontSize: "1rem",
                fontWeight: 600,
                marginTop: "0.2rem",
                color: "var(--text-primary, #c9d1d9)",
              }}
            >
              {scheduledWorkflowsSummary.totalCount ?? 0} workflows
            </div>
          </div>
        )}

        {diagnosticsSummary && (
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>
              Diagnostics
            </div>
            <div
              style={{
                fontSize: "1rem",
                fontWeight: 600,
                marginTop: "0.2rem",
                color: "var(--text-primary, #c9d1d9)",
              }}
            >
              {diagnosticsSummary.memoryMb !== undefined
                ? `${diagnosticsSummary.memoryMb} MB`
                : "Healthy"}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}

export default OperationsStatusBanner;
