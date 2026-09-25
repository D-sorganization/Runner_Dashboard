import React from "react";
import type { OrchestrationAuditEntry } from "./deployTypes";

export interface OperationsDeployAuditLogProps {
  auditLog: OrchestrationAuditEntry[];
}

export function OperationsDeployAuditLog({
  auditLog,
}: OperationsDeployAuditLogProps): React.ReactElement | null {
  if (auditLog.length === 0) return null;

  return (
    <div
      style={{
        padding: "0.875rem",
        borderRadius: "6px",
        background: "var(--bg-tertiary, #21262d)",
        border: "1px solid var(--border-subtle, #30363d)",
      }}
    >
      <div
        style={{
          fontSize: "0.8125rem",
          fontWeight: 600,
          color: "var(--text-primary, #c9d1d9)",
          marginBottom: "0.5rem",
        }}
      >
        Orchestration Audit Log
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
        {auditLog.slice(0, 5).map((log, idx) => (
          <div
            key={log.audit_id || idx}
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: "0.75rem",
              color: "var(--text-secondary, #8b949e)",
              padding: "0.25rem 0",
              borderBottom: idx < 4 ? "1px solid var(--border-subtle, #30363d)" : "none",
            }}
          >
            <span>
              <strong style={{ color: "var(--text-primary, #c9d1d9)" }}>
                {log.orchestration_type || "action"}
              </strong>
              : {log.deploy_action || log.workflow || "deploy"} on <code>{log.machine}</code>
            </span>
            <span
              style={{
                padding: "0.1rem 0.35rem",
                borderRadius: "4px",
                background: "var(--badge-neutral-bg, rgba(110,118,129,0.2))",
              }}
            >
              {log.decision || "accepted"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default OperationsDeployAuditLog;
