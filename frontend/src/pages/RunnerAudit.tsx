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
import { Badge } from "../primitives/Badge";
import { EmptyState } from "../primitives/EmptyState";
import { TouchButton } from "../primitives/TouchButton";
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

export default function RunnerAudit({
  audit,
  onRefresh,
}: RunnerAuditProps): React.ReactElement {
  const violations = audit.violations ?? [];
  const hasViolations = violations.length > 0;

  return (
    <div className="section runner-audit">
      <div className="section-header runner-audit__header">
        <div className="section-title">
          <span aria-hidden="true" className="runner-audit__title-icon">
            ⚠️
          </span>
          Hosted-Runner Billing Audit
        </div>
        <div className="runner-audit__actions">
          {audit.last_checked ? (
            <span className="runner-audit__meta">
              {"Last checked: " + new Date(audit.last_checked).toLocaleString()}
            </span>
          ) : (
            <Badge tone="neutral" size="sm">
              Not yet checked
            </Badge>
          )}
          <TouchButton
            className="runner-audit__refresh"
            onClick={onRefresh}
            type="button"
            aria-label="Refresh runner audit now"
          >
            <RefreshGlyph size={12} /> Refresh Now
          </TouchButton>
        </div>
      </div>

      {audit.error ? (
        <div role="status" className="runner-audit__error">
          {"Error: " + audit.error}
        </div>
      ) : null}

      {hasViolations ? (
        <div>
          <div className="runner-audit__warning">
            <strong className="runner-audit__warning-count">
              {violations.length + " violation(s) found. "}
            </strong>
            These jobs ran on GitHub-hosted runners and may incur unexpected
            billing costs.
          </div>
          <div className="runner-audit__table-wrap">
            <table className="runner-audit__table">
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>Workflow</th>
                  <th>Job</th>
                  <th>Runner</th>
                  <th>Started</th>
                  <th>Link</th>
                </tr>
              </thead>
              <tbody>
                {violations.map((v, i) => (
                  <tr key={i}>
                    <td className="runner-audit__repo-cell">{v.repo}</td>
                    <td className="runner-audit__muted-cell">{v.workflow}</td>
                    <td className="runner-audit__muted-cell">{v.job_name}</td>
                    <td>
                      <Badge
                        className="runner-audit__runner-badge"
                        tone="danger"
                        size="sm"
                      >
                        {v.runner_name || v.runner_group || "unknown"}
                      </Badge>
                    </td>
                    <td className="runner-audit__time-cell">
                      {v.started_at
                        ? new Date(v.started_at).toLocaleString()
                        : "—"}
                    </td>
                    <td>
                      {v.run_url ? (
                        <a
                          href={v.run_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="runner-audit__run-link"
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
        <EmptyState
          icon={audit.last_checked ? "✓" : "↻"}
          title={
            audit.last_checked
              ? "No hosted-runner violations detected"
              : "Audit has not run yet"
          }
          description={
            audit.last_checked
              ? "All recent jobs ran on self-hosted runners."
              : 'Click "Refresh Now" to trigger an immediate check.'
          }
        />
      )}
    </div>
  );
}
