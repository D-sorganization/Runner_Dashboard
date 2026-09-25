import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";
import type {
  DiagnosticsSummary,
  GitDrift,
  LauncherResult,
  OperationsDiagnosticsSectionProps,
  RestartResult,
} from "./diagnosticsTypes";

export type {
  DiagnosticsSummary,
  GitDrift,
  LauncherResult,
  OperationsDiagnosticsSectionProps,
  RestartResult,
};

export function OperationsDiagnosticsSection({
  initialSummary,
  onSummaryChange,
}: OperationsDiagnosticsSectionProps): React.ReactElement {
  const [data, setData] = useState<DiagnosticsSummary | null>(initialSummary ?? null);
  const [driftData, setDriftData] = useState<GitDrift | null>(null);
  const [loading, setLoading] = useState(initialSummary === undefined);
  const [error, setError] = useState<string | null>(null);

  const [restartConfirm, setRestartConfirm] = useState(false);
  const [restartLoading, setRestartLoading] = useState(false);
  const [restartResult, setRestartResult] = useState<RestartResult | null>(null);

  const [launcherLoading, setLauncherLoading] = useState(false);
  const [launcherResult, setLauncherResult] = useState<LauncherResult | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      legacyFetch("/api/diagnostics/summary").then(
        (r) => r.json() as Promise<DiagnosticsSummary>,
      ),
      legacyFetch("/api/deployment/git-drift")
        .then((r) => r.json() as Promise<GitDrift>)
        .catch(() => ({})),
    ])
      .then(([summary, drift]) => {
        setData(summary || {});
        setDriftData(drift || {});
        onSummaryChange?.(summary || {});
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Diagnostics error");
      })
      .finally(() => {
        setLoading(false);
      });
  }, [onSummaryChange]);

  useEffect(() => {
    if (initialSummary === undefined) {
      load();
    }
  }, [load, initialSummary]);

  const handleRestart = () => {
    setRestartLoading(true);
    setRestartResult(null);
    legacyFetch("/api/diagnostics/restart-service", { method: "POST" })
      .then((r) => r.json() as Promise<RestartResult>)
      .then((res) => {
        setRestartResult(res);
        setRestartConfirm(false);
      })
      .catch((err: unknown) => {
        setRestartResult({
          success: false,
          output: err instanceof Error ? err.message : "Restart failed",
        });
      })
      .finally(() => {
        setRestartLoading(false);
      });
  };

  const handleGenerateLaunchers = () => {
    setLauncherLoading(true);
    setLauncherResult(null);
    legacyFetch("/api/launchers/generate", { method: "POST" })
      .then((r) => r.json() as Promise<LauncherResult>)
      .then((res) => {
        setLauncherResult(res);
      })
      .catch((err: unknown) => {
        setLauncherResult({
          message: err instanceof Error ? err.message : "Generation failed",
        });
      })
      .finally(() => {
        setLauncherLoading(false);
      });
  };

  return (
    <section
      id="diagnostics"
      aria-labelledby="heading-diagnostics"
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
          marginBottom: "1rem",
        }}
      >
        <div>
          <h2
            id="heading-diagnostics"
            style={{
              fontSize: "1.25rem",
              fontWeight: 600,
              margin: "0 0 0.25rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Diagnostics
          </h2>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
              margin: 0,
            }}
          >
            Dashboard diagnostics, memory, WSL status, service recovery, and launchers
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            onClick={load}
            disabled={loading}
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
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            marginBottom: "1rem",
            borderRadius: "6px",
            background: "var(--bg-danger-subtle, rgba(248,81,73,0.1))",
            border: "1px solid var(--border-danger, #f85149)",
            color: "var(--text-danger, #f85149)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>Failed to load diagnostics: {error}</span>
          <button
            type="button"
            onClick={load}
            style={{
              padding: "0.25rem 0.6rem",
              fontSize: "0.75rem",
              fontWeight: 600,
              borderRadius: "4px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* System stats grid */}
      {data && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: "0.75rem",
            marginBottom: "1rem",
          }}
        >
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>MEMORY</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              {data.dashboard_memory_mb ?? "—"} MB
            </div>
          </div>
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>PID</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              PID {data.dashboard_pid ?? "—"}
            </div>
          </div>
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>PORT</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              Port {data.dashboard_port ?? 8000}
            </div>
          </div>
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>WSL ENVIRONMENT</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              WSL: {data.wsl_status || (data.wsl_available ? "available" : "n/a")}
            </div>
          </div>
          <div
            style={{
              padding: "0.625rem 0.875rem",
              borderRadius: "6px",
              background: "var(--bg-tertiary, #21262d)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>GIT COMMIT</div>
            <div style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              <code>{data.git_commit?.slice(0, 7) || "dev"}</code>
              {(data.is_drifted || driftData?.is_drifted) && (
                <span
                  style={{
                    marginLeft: "0.4rem",
                    fontSize: "0.7rem",
                    padding: "0.1rem 0.35rem",
                    borderRadius: "4px",
                    background: "var(--badge-warning-bg, rgba(210,153,34,0.15))",
                    color: "var(--badge-warning-text, #d29922)",
                  }}
                >
                  drifted
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Recovery Actions & Launchers */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "1rem",
          marginBottom: "1rem",
        }}
      >
        {/* Service Recovery */}
        <div
          style={{
            padding: "1rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontWeight: 600, color: "var(--text-primary, #c9d1d9)", marginBottom: "0.5rem" }}>
            Service Recovery
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--text-muted, #8b949e)", margin: "0 0 0.75rem 0" }}>
            Restart the systemd dashboard daemon if the service has wedged.
          </p>

          {!restartConfirm ? (
            <button
              type="button"
              onClick={() => setRestartConfirm(true)}
              style={{
                padding: "0.4rem 0.75rem",
                fontSize: "0.8125rem",
                fontWeight: 500,
                borderRadius: "6px",
                cursor: "pointer",
                background: "var(--bg-tertiary, #21262d)",
                color: "var(--text-primary, #c9d1d9)",
                border: "1px solid var(--border-color, #30363d)",
              }}
            >
              Restart Service
            </button>
          ) : (
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button
                type="button"
                onClick={handleRestart}
                disabled={restartLoading}
                style={{
                  padding: "0.4rem 0.75rem",
                  fontSize: "0.8125rem",
                  fontWeight: 600,
                  borderRadius: "6px",
                  cursor: "pointer",
                  background: "var(--badge-danger-bg, rgba(248,81,73,0.15))",
                  color: "var(--badge-danger-text, #f85149)",
                  border: "1px solid var(--border-red, #da3633)",
                }}
              >
                {restartLoading ? "Restarting..." : "Confirm Restart"}
              </button>
              <button
                type="button"
                onClick={() => setRestartConfirm(false)}
                disabled={restartLoading}
                style={{
                  padding: "0.4rem 0.75rem",
                  fontSize: "0.8125rem",
                  fontWeight: 500,
                  borderRadius: "6px",
                  cursor: "pointer",
                  background: "transparent",
                  color: "var(--text-muted, #8b949e)",
                  border: "none",
                }}
              >
                Cancel
              </button>
            </div>
          )}

          {restartResult && (
            <div
              style={{
                marginTop: "0.75rem",
                padding: "0.5rem 0.75rem",
                borderRadius: "4px",
                fontSize: "0.75rem",
                background: restartResult.success
                  ? "var(--badge-success-bg, rgba(46,160,67,0.15))"
                  : "var(--badge-danger-bg, rgba(248,81,73,0.15))",
                color: restartResult.success
                  ? "var(--badge-success-text, #3fb950)"
                  : "var(--badge-danger-text, #f85149)",
              }}
            >
              {restartResult.output || (restartResult.success ? "Service restarted successfully" : "Failed")}
            </div>
          )}
        </div>

        {/* Windows Launchers */}
        <div
          style={{
            padding: "1rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontWeight: 600, color: "var(--text-primary, #c9d1d9)", marginBottom: "0.5rem" }}>
            Windows Launcher Scripts
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--text-muted, #8b949e)", margin: "0 0 0.75rem 0" }}>
            Regenerate Windows desktop .bat launchers for runners and services.
          </p>

          <button
            type="button"
            onClick={handleGenerateLaunchers}
            disabled={launcherLoading}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
            }}
          >
            {launcherLoading ? "Generating..." : "Generate Launchers"}
          </button>

          {launcherResult && (
            <div
              style={{
                marginTop: "0.75rem",
                padding: "0.5rem 0.75rem",
                borderRadius: "4px",
                fontSize: "0.75rem",
                background: "var(--badge-neutral-bg, rgba(110,118,129,0.2))",
                color: "var(--text-primary, #c9d1d9)",
              }}
            >
              {launcherResult.message}
            </div>
          )}
        </div>
      </div>

      {/* Quick API Links */}
      <div
        style={{
          paddingTop: "0.75rem",
          borderTop: "1px solid var(--border-subtle, #30363d)",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "1rem",
          fontSize: "0.75rem",
          color: "var(--text-muted, #8b949e)",
        }}
      >
        <span>API Endpoints:</span>
        <a href="/api/health" target="_blank" rel="noopener noreferrer" style={{ color: "var(--text-link, #58a6ff)" }}>
          /api/health
        </a>
        <a href="/api/fleet/health" target="_blank" rel="noopener noreferrer" style={{ color: "var(--text-link, #58a6ff)" }}>
          /api/fleet/health
        </a>
        <a href="/api/diagnostics/summary" target="_blank" rel="noopener noreferrer" style={{ color: "var(--text-link, #58a6ff)" }}>
          /api/diagnostics/summary
        </a>
      </div>
    </section>
  );
}

export default OperationsDiagnosticsSection;
