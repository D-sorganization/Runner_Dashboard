/**
 * MobileRuns.tsx — Runs tab for the mobile Staff Console (SC-D8, Issue #1331).
 *
 * Split from ``Mobile.tsx`` to keep that module under the repo's 500-line soft
 * cap. Renders the filterable run list shown by the mobile "runs" view.
 */
import React, { useCallback, useEffect, useState } from "react";
import type { StaffRoleItem } from "./types";
import { Badge } from "../../primitives/Badge";
import { TimeAgo } from "../../primitives/TimeAgo";
import { fetchRuns, formatEffortUsd, statusTone, targetLabel, type RunRecord, RUN_STATUSES } from "../Staff/staffApi";
import "./mobile.css";

interface MobileRunsViewProps {
  roles: StaffRoleItem[];
  onOpenRun?: (runId: string) => void;
}

export const MobileRunsView: React.FC<MobileRunsViewProps> = ({ roles, onOpenRun }) => {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const loadRuns = useCallback(async (signal?: AbortSignal) => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetchRuns(
        {
          role: roleFilter || undefined,
          status: statusFilter || undefined,
          limit: 50,
        },
        signal,
      );
      setRuns(res.runs ?? []);
    } catch (err: unknown) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [roleFilter, statusFilter]);

  useEffect(() => {
    const ac = new AbortController();
    void loadRuns(ac.signal);
    return () => ac.abort();
  }, [loadRuns]);

  const handleOpenRun = (runId: string) => {
    if (onOpenRun) {
      onOpenRun(runId);
    } else if (typeof window !== "undefined") {
      window.location.href = `/staff?run=${encodeURIComponent(runId)}`;
    }
  };

  return (
    <div className="staff-mobile__runs-container" data-testid="staff-mobile-runs-view">
      <div className="staff-mobile__runs-filters">
        <select
          className="staff-mobile__runs-select"
          aria-label="Filter by role"
          value={roleFilter}
          onChange={(e) => setRoleFilter(e.target.value)}
        >
          <option value="">All Roles</option>
          {roles.map((r) => (
            <option key={r.name} value={r.name}>
              {r.title}
            </option>
          ))}
        </select>
        <select
          className="staff-mobile__runs-select"
          aria-label="Filter by status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All Statuses</option>
          {RUN_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="staff-mobile__skeleton" />
          <div className="staff-mobile__skeleton" />
          <div className="staff-mobile__skeleton" />
        </div>
      )}

      {error && !loading && (
        <div className="console-error-banner" role="alert" style={{ margin: 0 }}>
          <span>Failed to load runs: {error}</span>
          <button
            type="button"
            className="console-error-banner__retry"
            onClick={() => void loadRuns()}
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && (!runs || runs.length === 0) && (
        <div
          style={{
            padding: "24px 16px",
            textAlign: "center",
            color: "var(--text-muted, #8b949e)",
            fontSize: 14,
          }}
        >
          No runs found.
        </div>
      )}

      {!loading && !error && runs && runs.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {runs.map((run) => (
            <div
              key={run.id}
              className="staff-mobile__run-card"
              role="button"
              tabIndex={0}
              data-testid={`staff-mobile-run-${run.id}`}
              onClick={() => handleOpenRun(run.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleOpenRun(run.id);
                }
              }}
            >
              <div className="staff-mobile__run-header">
                <span className="staff-mobile__run-role">{run.role}</span>
                <Badge tone={statusTone(run.status)} size="sm">
                  {run.status}
                </Badge>
              </div>
              <div className="staff-mobile__run-target">
                {run.repo ? `${run.repo} ` : ""}
                {targetLabel(run) ? `(${targetLabel(run)})` : run.id}
              </div>
              {run.last_line && (
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--text-secondary, #8b949e)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {run.last_line}
                </div>
              )}
              <div className="staff-mobile__run-footer">
                <span>{run.created_at ? <TimeAgo iso={run.created_at} /> : "just now"}</span>
                {run.cost_usd !== undefined && <span>{formatEffortUsd(run.cost_usd)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
