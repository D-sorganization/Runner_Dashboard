/**
 * RunCard.tsx — Structured card for live run progress, node/provider info,
 * expandable log tail, cancel controls, and links (SC-D5, Issue #1319).
 */
import React, { useState, useRef } from "react";
import type { RunCardProps } from "./cardTypes";
import { formatElapsedTime, getStatusBadgeStyle } from "./cardUtils";

export const RunCard: React.FC<RunCardProps> = ({
  run,
  onCancelRun,
  className = "",
}) => {
  const [logsExpanded, setLogsExpanded] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const hasCancelledRef = useRef(false);

  const statusStyle = getStatusBadgeStyle(run.status);
  const isTerminal = ["completed", "success", "failed", "cancelled", "error"].includes(
    (run.status || "").toLowerCase()
  );
  const canCancel = run.can_cancel ?? (!isTerminal && Boolean(onCancelRun));

  const logsArray = Array.isArray(run.log_tail)
    ? run.log_tail
    : typeof run.log_tail === "string"
    ? run.log_tail.split("\n")
    : [];

  const handleCancel = async () => {
    if (hasCancelledRef.current || isCancelling || !onCancelRun) return;
    hasCancelledRef.current = true;
    setIsCancelling(true);

    try {
      await onCancelRun(run.run_id);
    } finally {
      setIsCancelling(false);
    }
  };

  return (
    <div
      role="region"
      aria-label="Run progress"
      data-testid="run-card"
      className={`run-progress-card ${className}`}
      style={{
        background: "var(--bg-tertiary, #161b22)",
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "14px 16px",
        margin: "8px 0",
        maxWidth: 540,
        boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      }}
    >
      {/* Header: Run ID and Status */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace", color: "var(--text-primary, #c9d1d9)" }}>
            {run.run_id}
          </span>
          {run.run_url && (
            <a
              href={run.run_url}
              style={{ fontSize: 11, color: "var(--accent-blue, #58a6ff)", textDecoration: "none" }}
            >
              [View Run]
            </a>
          )}
        </div>
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            padding: "2px 8px",
            borderRadius: 12,
            background: statusStyle.bg,
            color: statusStyle.color,
            border: statusStyle.border,
            textTransform: "uppercase",
          }}
        >
          {run.status}
        </span>
      </div>

      {/* Meta Grid: Node, Provider, Elapsed Time, PR */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
          gap: "6px 12px",
          fontSize: 12,
          color: "var(--text-secondary, #8b949e)",
          marginBottom: 10,
        }}
      >
        {run.node && (
          <div>
            <strong>Node:</strong> <span style={{ color: "var(--text-primary, #c9d1d9)" }}>{run.node}</span>
          </div>
        )}
        {run.provider && (
          <div>
            <strong>Provider:</strong> <span style={{ color: "var(--text-primary, #c9d1d9)" }}>{run.provider}</span>
          </div>
        )}
        {run.elapsed_seconds !== undefined && (
          <div>
            <strong>Elapsed:</strong>{" "}
            <span style={{ color: "var(--text-primary, #c9d1d9)" }}>{formatElapsedTime(run.elapsed_seconds)}</span>
          </div>
        )}
        {run.pr && (
          <div>
            <strong>PR:</strong>{" "}
            <a
              href={run.pr_url || `#`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "underline" }}
            >
              #{run.pr}
            </a>
          </div>
        )}
      </div>

      {/* Expandable Log Tail */}
      {logsArray.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            data-testid="log-toggle-btn"
            onClick={() => setLogsExpanded(!logsExpanded)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--accent-blue, #58a6ff)",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
              padding: "4px 0",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span>{logsExpanded ? "▼ Hide log tail" : "▶ Show log tail"}</span>
            <span style={{ color: "var(--text-muted, #8b949e)", fontSize: 10 }}>({logsArray.length} lines)</span>
          </button>

          {logsExpanded && (
            <pre
              data-testid="log-tail-content"
              style={{
                background: "var(--bg-primary, #0d1117)",
                color: "var(--text-secondary, #8b949e)",
                fontFamily: "monospace",
                fontSize: 11,
                padding: "8px 10px",
                borderRadius: 4,
                maxHeight: 160,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                margin: "4px 0 0 0",
                lineHeight: 1.4,
              }}
            >
              {logsArray.join("\n")}
            </pre>
          )}
        </div>
      )}

      {/* Cancel Action */}
      {canCancel && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
          <button
            type="button"
            data-testid="cancel-run-btn"
            disabled={isCancelling}
            onClick={handleCancel}
            style={{
              background: "rgba(248, 81, 73, 0.15)",
              color: "var(--accent-red, #f85149)",
              border: "1px solid rgba(248, 81, 73, 0.4)",
              borderRadius: 6,
              padding: "4px 12px",
              fontSize: 11,
              fontWeight: 600,
              cursor: isCancelling ? "not-allowed" : "pointer",
              opacity: isCancelling ? 0.6 : 1,
            }}
          >
            ■ Cancel Run
          </button>
        </div>
      )}
    </div>
  );
};
