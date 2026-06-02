/**
 * RunnerAudit.tsx — the "Runner Audit" tab, extracted verbatim (behaviour-wise)
 * from the legacy `App.tsx` monolith as part of the decomposition epic (#836,
 * pass 2).
 *
 * Surfaces the hosted-runner billing audit: jobs that ran on GitHub-hosted
 * runners (and may therefore incur billing) instead of self-hosted fleet
 * runners. Read-only plus a "Refresh Now" trigger that asks the backend to
 * re-run the audit.
 *
 * The audit data (and its refresh trigger) are owned by the legacy App because
 * the same `runnerAudit` state also feeds the Fleet hero alert and the sidebar
 * violation badge. To stay DRY and avoid double-polling, this page is
 * presentational: it receives the already-fetched `audit` payload and an
 * `onRefresh` callback. Loading/empty/error states and a11y semantics match the
 * original legacy render exactly.
 */
import React from "react";
import { RefreshGlyph } from "./decompIcons";

/** A single hosted-runner routing violation row. */
export interface RunnerAuditViolation {
  repo?: string;
  workflow?: string;
  job_name?: string;
  runner_name?: string;
  runner_group?: string;
  started_at?: string | null;
  run_url?: string | null;
}

/** The audit payload owned by the legacy App and passed down here. */
export interface RunnerAuditData {
  violations?: RunnerAuditViolation[];
  last_checked?: string | null;
  error?: string | null;
}

export interface RunnerAuditProps {
  /** The audit payload (violations + last_checked + error). */
  audit: RunnerAuditData;
  /** Trigger an immediate backend re-check. */
  onRefresh: () => void;
}

const thStyle: React.CSSProperties = {
  padding: "8px 12px",
  color: "var(--text-secondary)",
  fontWeight: 600,
};

export default function RunnerAudit({
  audit,
  onRefresh,
}: RunnerAuditProps): React.ReactElement {
  const violations = audit.violations ?? [];
  const hasViolations = violations.length > 0;

  return (
    <div className="section" style={{ padding: "24px" }}>
      <div
        className="section-header"
        style={{
          marginBottom: "16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div className="section-title">
          <span aria-hidden="true" style={{ fontSize: "18px", marginRight: "8px" }}>
            ⚠️
          </span>
          Hosted-Runner Billing Audit
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          {audit.last_checked ? (
            <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              {"Last checked: " + new Date(audit.last_checked).toLocaleString()}
            </span>
          ) : (
            <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              Not yet checked
            </span>
          )}
          <button
            className="btn"
            style={{ fontSize: "12px" }}
            onClick={onRefresh}
            type="button"
            aria-label="Refresh runner audit now"
          >
            <RefreshGlyph size={12} /> Refresh Now
          </button>
        </div>
      </div>

      {audit.error ? (
        <div
          role="status"
          style={{
            color: "var(--accent-red)",
            marginBottom: "12px",
            fontSize: "13px",
          }}
        >
          {"Error: " + audit.error}
        </div>
      ) : null}

      {hasViolations ? (
        <div>
          <div
            style={{
              marginBottom: "12px",
              padding: "10px 14px",
              background: "rgba(248,81,73,0.1)",
              border: "1px solid rgba(248,81,73,0.3)",
              borderRadius: "6px",
              fontSize: "13px",
            }}
          >
            <strong style={{ color: "var(--accent-red)" }}>
              {violations.length + " violation(s) found. "}
            </strong>
            These jobs ran on GitHub-hosted runners and may incur unexpected
            billing costs.
          </div>
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "13px",
              }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid var(--border)",
                    textAlign: "left",
                  }}
                >
                  <th style={thStyle}>Repo</th>
                  <th style={thStyle}>Workflow</th>
                  <th style={thStyle}>Job</th>
                  <th style={thStyle}>Runner</th>
                  <th style={thStyle}>Started</th>
                  <th style={thStyle}>Link</th>
                </tr>
              </thead>
              <tbody>
                {violations.map((v, i) => (
                  <tr
                    key={i}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)",
                    }}
                  >
                    <td style={{ padding: "8px 12px", fontWeight: 500 }}>{v.repo}</td>
                    <td style={{ padding: "8px 12px", color: "var(--text-secondary)" }}>
                      {v.workflow}
                    </td>
                    <td style={{ padding: "8px 12px", color: "var(--text-secondary)" }}>
                      {v.job_name}
                    </td>
                    <td style={{ padding: "8px 12px" }}>
                      <span
                        style={{
                          background: "rgba(248,81,73,0.15)",
                          color: "var(--accent-red)",
                          padding: "2px 6px",
                          borderRadius: "4px",
                          fontSize: "11px",
                          fontFamily: "monospace",
                        }}
                      >
                        {v.runner_name || v.runner_group || "unknown"}
                      </span>
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        color: "var(--text-muted)",
                        fontSize: "12px",
                      }}
                    >
                      {v.started_at ? new Date(v.started_at).toLocaleString() : "—"}
                    </td>
                    <td style={{ padding: "8px 12px" }}>
                      {v.run_url ? (
                        <a
                          href={v.run_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{
                            color: "var(--accent-blue)",
                            textDecoration: "none",
                            fontSize: "12px",
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
      ) : (
        <div
          style={{
            textAlign: "center",
            padding: "48px 24px",
            color: "var(--text-muted)",
            fontSize: "14px",
          }}
        >
          <div aria-hidden="true" style={{ fontSize: "32px", marginBottom: "12px" }}>
            ✓
          </div>
          {audit.last_checked
            ? "No hosted-runner violations detected. All recent jobs ran on self-hosted runners."
            : 'Audit has not run yet. Click "Refresh Now" to trigger an immediate check.'}
        </div>
      )}
    </div>
  );
}
