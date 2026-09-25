import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";
import type {
  DeploymentMachine,
  DeploymentStateData,
  FleetOrchestrationData,
  OperationsDeploySectionProps,
  OrchestrationAuditEntry,
  OrchestrationMachine,
} from "./deployTypes";
import { OperationsDeployAuditLog } from "./OperationsDeployAuditLog";

export type {
  DeploymentMachine,
  DeploymentStateData,
  FleetOrchestrationData,
  OperationsDeploySectionProps,
  OrchestrationAuditEntry,
  OrchestrationMachine,
};

export function OperationsDeploySection({
  initialDeployData,
  initialOrchData,
  onDeployDataChange,
  onOrchDataChange,
}: OperationsDeploySectionProps): React.ReactElement {
  const [deployData, setDeployData] = useState<DeploymentStateData | null>(initialDeployData ?? null);
  const [orchData, setOrchData] = useState<FleetOrchestrationData | null>(initialOrchData ?? null);
  const [loading, setLoading] = useState(initialDeployData === undefined);
  const [error, setError] = useState<string | null>(null);

  // Form states
  const [deployMachine, setDeployMachine] = useState("");
  const [deployAction, setDeployAction] = useState("");
  const [deployConfirmed, setDeployConfirmed] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployMessage, setDeployMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      legacyFetch("/api/deployment/state").then((r) => {
        if (!r.ok) throw new Error(`Deployment HTTP ${r.status}`);
        return r.json() as Promise<DeploymentStateData>;
      }),
      legacyFetch("/api/fleet/orchestration")
        .then((r) => {
          if (!r.ok) return { machines: [], audit_log: [] };
          return r.json() as Promise<FleetOrchestrationData>;
        })
        .catch(() => ({ machines: [], audit_log: [] })),
    ])
      .then(([dep, orch]) => {
        setDeployData(dep);
        setOrchData(orch);
        if (dep?.machines && dep.machines.length > 0 && !deployMachine) {
          setDeployMachine(dep.machines[0].name);
        }
        onDeployDataChange?.(dep);
        onOrchDataChange?.(orch);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load deployment");
      })
      .finally(() => {
        setLoading(false);
      });
  }, [deployMachine, onDeployDataChange, onOrchDataChange]);

  useEffect(() => {
    if (initialDeployData === undefined) {
      load();
    }
  }, [load, initialDeployData]);

  const handleExecuteDeploy = (e: React.FormEvent) => {
    e.preventDefault();
    if (!deployAction || !deployConfirmed) return;
    setDeployBusy(true);
    setDeployMessage(null);

    legacyFetch("/api/fleet/orchestration/deploy", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        machine: deployMachine || (deployData?.machines?.[0]?.name ?? "controltower"),
        action: deployAction,
        confirmed: deployConfirmed,
      }),
    })
      .then((r) => r.json() as Promise<{ message?: string }>)
      .then((res) => {
        setDeployMessage(res.message || "Deploy action dispatched successfully");
        setDeployAction("");
        setDeployConfirmed(false);
        load();
      })
      .catch((err: unknown) => {
        setDeployMessage(err instanceof Error ? err.message : "Deploy action failed");
      })
      .finally(() => {
        setDeployBusy(false);
      });
  };

  const machines = deployData?.machines || [];
  const auditLog = orchData?.audit_log || [];

  return (
    <section
      id="deploy"
      aria-labelledby="heading-deploy"
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
            id="heading-deploy"
            style={{
              fontSize: "1.25rem",
              fontWeight: 600,
              margin: "0 0 0.25rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Deploy & versions
          </h2>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
              margin: 0,
            }}
          >
            Rollout status, version drift, and multi-node orchestration controls
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
          <span>Failed to load deployment: {error}</span>
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

      {/* Version summary cards */}
      {deployData && (
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
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>EXPECTED VERSION</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              {deployData.expected_version || "—"}
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
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>ROLLOUT STATUS</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              {deployData.rollout_state?.status || "steady"}
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
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>ATTENTION NEEDED</div>
            <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
              {deployData.rollout_state?.machines_attention ?? 0} machines
            </div>
          </div>
        </div>
      )}

      {/* Machines deployment table */}
      <div
        style={{
          border: "1px solid var(--border-color, #30363d)",
          borderRadius: "6px",
          overflow: "hidden",
          background: "var(--bg-tertiary, #21262d)",
          marginBottom: "1rem",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-color, #30363d)", textAlign: "left" }}>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>MACHINE</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>DEPLOYED</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>DESIRED</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>DRIFT / STATUS</th>
            </tr>
          </thead>
          <tbody>
            {machines.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: "1.5rem", textAlign: "center", color: "var(--text-muted, #8b949e)" }}>
                  No deployment machine records found.
                </td>
              </tr>
            ) : (
              machines.map((m, idx) => (
                <tr
                  key={m.name || idx}
                  style={{
                    borderBottom: idx < machines.length - 1 ? "1px solid var(--border-subtle, #30363d)" : "none",
                  }}
                >
                  <td style={{ padding: "0.625rem 0.875rem", fontWeight: 500, color: "var(--text-primary, #c9d1d9)" }}>
                    {m.display_name || m.name}
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-primary, #c9d1d9)" }}>
                    <code>{m.deployed_version || "—"}</code>
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-secondary, #8b949e)" }}>
                    <code>{m.desired_version || "—"}</code>
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem" }}>
                    {m.drift_status?.message ? (
                      <span
                        style={{
                          padding: "0.15rem 0.5rem",
                          borderRadius: "4px",
                          fontSize: "0.75rem",
                          fontWeight: 500,
                          background:
                            m.drift_status.severity === "danger"
                              ? "var(--badge-danger-bg, rgba(248,81,73,0.15))"
                              : "var(--badge-warning-bg, rgba(210,153,34,0.15))",
                          color:
                            m.drift_status.severity === "danger"
                              ? "var(--badge-danger-text, #f85149)"
                              : "var(--badge-warning-text, #d29922)",
                        }}
                      >
                        {m.drift_status.message}
                      </span>
                    ) : (
                      <span
                        style={{
                          padding: "0.15rem 0.5rem",
                          borderRadius: "4px",
                          fontSize: "0.75rem",
                          fontWeight: 500,
                          background: "var(--badge-success-bg, rgba(46,160,67,0.15))",
                          color: "var(--badge-success-text, #3fb950)",
                        }}
                      >
                        in sync
                      </span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Orchestration Deploy Actions Form */}
      <div
        style={{
          padding: "1rem",
          borderRadius: "6px",
          background: "var(--bg-tertiary, #21262d)",
          border: "1px solid var(--border-subtle, #30363d)",
          marginBottom: "1rem",
        }}
      >
        <div style={{ fontWeight: 600, color: "var(--text-primary, #c9d1d9)", marginBottom: "0.5rem" }}>
          Dispatch Multi-Node Deploy Action
        </div>
        <form onSubmit={handleExecuteDeploy} style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", alignItems: "center" }}>
          {machines.length > 0 && (
            <select
              aria-label="Target machine"
              value={deployMachine}
              onChange={(e) => setDeployMachine(e.target.value)}
              style={{
                padding: "0.4rem 0.6rem",
                borderRadius: "6px",
                background: "var(--bg-secondary, #161b22)",
                color: "var(--text-primary, #c9d1d9)",
                border: "1px solid var(--border-color, #30363d)",
              }}
            >
              {machines.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.display_name || m.name}
                </option>
              ))}
            </select>
          )}

          <input
            type="text"
            placeholder="e.g. restart_runner"
            aria-label="Deploy action"
            value={deployAction}
            onChange={(e) => setDeployAction(e.target.value)}
            style={{
              padding: "0.4rem 0.6rem",
              borderRadius: "6px",
              background: "var(--bg-secondary, #161b22)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
              minWidth: "180px",
            }}
          />

          <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.8125rem", color: "var(--text-secondary, #8b949e)" }}>
            <input
              type="checkbox"
              aria-label="Confirm action"
              checked={deployConfirmed}
              onChange={(e) => setDeployConfirmed(e.target.checked)}
            />
            Confirm action
          </label>

          <button
            type="submit"
            disabled={deployBusy || !deployAction || !deployConfirmed}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 600,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--btn-primary-bg, #238636)",
              color: "var(--btn-primary-text, #ffffff)",
              border: "1px solid var(--border-green, #2ea043)",
            }}
          >
            {deployBusy ? "Dispatching..." : "Execute Deploy"}
          </button>
        </form>

        {deployMessage && (
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
            {deployMessage}
          </div>
        )}
      </div>

      {/* Audit Log (Last 5) */}
      <OperationsDeployAuditLog auditLog={auditLog} />
    </section>
  );
}

export default OperationsDeploySection;
