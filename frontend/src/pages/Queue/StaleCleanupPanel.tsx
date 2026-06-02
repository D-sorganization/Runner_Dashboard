// StaleCleanupPanel – preview and bulk-cancel stale queued runs.
//
// Migrated off the legacy `React.createElement` / `any` pattern (issue #841)
// to typed TSX. Behaviour and DOM structure are preserved so the existing
// QueueTab tests and operator workflow are unchanged.

import { useState, type CSSProperties } from "react";

import { Badge } from "../../primitives/Badge";
import { Collapse } from "../../components/Collapse";
import {
  STALE_REASONS,
  formatReason,
  normalizeStalePayload,
  type InlineMessage,
  type NormalizedStalePayload,
  type StaleRun,
} from "./types";

interface StaleCleanupPanelProps {
  onRefresh?: () => void;
}

const inputStyle: CSSProperties = {
  minWidth: 120,
  padding: "7px 9px",
  borderRadius: 6,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const mutedSha: CSSProperties = { color: "var(--text-muted)", fontSize: 12 };

interface StaleFilters {
  min_age_minutes: number;
  repo: string;
  workflow: string;
  max_count: number;
  safe_only: true;
}

export function StaleCleanupPanel({ onRefresh }: StaleCleanupPanelProps) {
  const [minAge, setMinAge] = useState("60");
  const [repoFilter, setRepoFilter] = useState("");
  const [workflowFilter, setWorkflowFilter] = useState("");
  const [maxCount, setMaxCount] = useState("25");
  const [preview, setPreview] = useState<NormalizedStalePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [purging, setPurging] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<InlineMessage | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const runs: StaleRun[] = preview?.runs ?? [];
  const safeRuns = runs.filter((run) => run.safe_to_cancel === true);
  const max = Math.max(0, Number(maxCount) || 0);
  const purgeTargets = max > 0 ? safeRuns.slice(0, max) : [];

  function filters(): StaleFilters {
    return {
      min_age_minutes: Math.max(1, Number(minAge) || 60),
      repo: repoFilter.trim(),
      workflow: workflowFilter.trim(),
      max_count: max,
      safe_only: true,
    };
  }

  function previewStale(): void {
    const f = filters();
    const params = new URLSearchParams();
    params.set("min_age_minutes", String(f.min_age_minutes));
    if (f.repo) params.set("repo", f.repo);
    if (f.workflow) params.set("workflow", f.workflow);
    if (f.max_count > 0) params.set("max_count", String(f.max_count));
    params.set("safe_only", "true");
    setLoading(true);
    setConfirming(false);
    setMessage(null);
    fetch("/api/queue/stale?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r) => {
        if (!r.ok) throw new Error("Preview failed");
        return r.json();
      })
      .then((d) => {
        setPreview(normalizeStalePayload(d));
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
        setMessage({ type: "error", text: "Stale preview failed" });
      });
  }

  function purgeStale(): void {
    if (!confirming) {
      setConfirming(true);
      setTimeout(() => {
        setConfirming((cur) => (cur ? false : cur));
      }, 6000);
      return;
    }
    const f = filters();
    setPurging(true);
    setConfirming(false);
    setMessage(null);
    fetch("/api/queue/purge-stale", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        min_age_minutes: f.min_age_minutes,
        repo: f.repo || null,
        workflow: f.workflow || null,
        max_count: f.max_count,
        safe_only: true,
        dry_run: false,
        run_ids: purgeTargets.map((run) => run.run_id),
      }),
    })
      .then((r) => {
        if (!r.ok) throw new Error("Purge failed");
        return r.json();
      })
      .then((d) => {
        const normalized = normalizeStalePayload(d);
        setPreview(normalized);
        setPurging(false);
        setMessage({
          type: normalized.errors.length > 0 ? "error" : "success",
          text:
            "Cancelled " +
            normalized.cancelled_count +
            " stale run(s)" +
            (normalized.errors.length > 0
              ? " with " + normalized.errors.length + " error(s)"
              : ""),
        });
        if (onRefresh) setTimeout(onRefresh, 1500);
      })
      .catch(() => {
        setPurging(false);
        setMessage({ type: "error", text: "Stale purge failed" });
      });
  }

  function renderInput(
    label: string,
    value: string,
    setter: (next: string) => void,
    type?: string,
  ) {
    return (
      <label
        style={{
          display: "grid",
          gap: 4,
          fontSize: 12,
          color: "var(--text-muted)",
        }}
      >
        {label}
        <input
          value={value}
          type={type ?? "text"}
          onChange={(e) => setter(e.target.value)}
          style={inputStyle}
        />
      </label>
    );
  }

  function renderReasonCounts() {
    const counts = preview?.reason_counts ?? {};
    return (
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        {STALE_REASONS.map((reason) => (
          <span
            key={reason}
            style={{
              padding: "3px 8px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              color: "var(--text-secondary)",
              fontSize: 12,
            }}
          >
            {formatReason(reason)}: <b>{counts[reason] || 0}</b>
          </span>
        ))}
      </div>
    );
  }

  function renderRun(run: StaleRun, mobile: boolean) {
    const key = run.repo + "/" + run.run_id;
    const open = !!expanded[key];
    const safe = run.safe_to_cancel === true;
    const reasonBadge = (
      <Badge tone={safe ? "success" : "warning"}>{formatReason(run.reason)}</Badge>
    );

    if (mobile) {
      return (
        <div key={"stale-mobile-" + key} className="mobile-run-card">
          <button
            type="button"
            onClick={() =>
              setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))
            }
            style={{
              width: "100%",
              textAlign: "left",
              background: "none",
              border: 0,
              color: "inherit",
              padding: 0,
              cursor: "pointer",
            }}
          >
            <div className="mobile-run-title">{run.workflow}</div>
            <div className="mobile-run-meta">
              <span>{run.repo}</span>
              <span>{run.branch}</span>
              <span>{run.age_minutes != null ? run.age_minutes + "m" : "-"}</span>
            </div>
            <div
              style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}
            >
              {reasonBadge}
              <Badge tone={safe ? "success" : "danger"}>
                {safe ? "safe to cancel" : "review first"}
              </Badge>
            </div>
          </button>
          {open ? (
            <div
              style={{ marginTop: 10, fontSize: 12, color: "var(--text-muted)" }}
            >
              <div>PR: {run.pr_number || "-"}</div>
              <div>Run SHA: {run.run_head_sha || "-"}</div>
              <div>Current SHA: {run.current_head_sha || "-"}</div>
            </div>
          ) : null}
        </div>
      );
    }

    return (
      <tr key={"stale-" + key}>
        <td>{run.repo}</td>
        <td>{run.workflow}</td>
        <td>{run.branch}</td>
        <td>{run.pr_number || "-"}</td>
        <td>{run.age_minutes != null ? run.age_minutes + "m" : "-"}</td>
        <td>{reasonBadge}</td>
        <td>
          <Badge tone={safe ? "success" : "danger"}>
            {safe ? "safe" : "blocked"}
          </Badge>
        </td>
        <td style={mutedSha}>{run.current_head_sha || "-"}</td>
        <td style={mutedSha}>{run.run_head_sha || "-"}</td>
        <td>
          {run.run_url ? (
            <a
              href={run.run_url}
              target="_blank"
              rel="noopener"
              style={{
                color: "var(--accent-blue)",
                textDecoration: "none",
                fontSize: 12,
              }}
            >
              View
            </a>
          ) : (
            "-"
          )}
        </td>
      </tr>
    );
  }

  return (
    <Collapse
      title="Stale Cleanup"
      icon={<span className="queue-dot waiting" style={{ marginRight: 4 }} />}
      badge={preview ? preview.stale_count + " stale" : "preview"}
      defaultOpen
    >
      <div>
        {message ? (
          <div
            role="alert"
            style={{
              margin: "0 0 12px",
              padding: "10px 12px",
              borderRadius: 6,
              background:
                message.type === "error"
                  ? "rgba(248,81,73,0.15)"
                  : "rgba(63,185,80,0.15)",
              color:
                message.type === "error"
                  ? "var(--accent-red)"
                  : "var(--accent-green)",
              border:
                "1px solid " +
                (message.type === "error"
                  ? "var(--accent-red)"
                  : "var(--accent-green)"),
              fontSize: 13,
            }}
          >
            {message.text}
          </div>
        ) : null}

        <div
          style={{
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
            alignItems: "end",
          }}
        >
          {renderInput("Min age (minutes)", minAge, setMinAge, "number")}
          {renderInput("Repo filter", repoFilter, setRepoFilter)}
          {renderInput("Workflow filter", workflowFilter, setWorkflowFilter)}
          {renderInput("Max cancellations", maxCount, setMaxCount, "number")}
          <button className="btn" onClick={previewStale} disabled={loading}>
            {loading ? <span className="spinner" /> : "Preview stale"}
          </button>
          <button
            className="btn"
            onClick={purgeStale}
            disabled={purging || purgeTargets.length === 0}
            style={{
              border: "1px solid var(--accent-red)",
              color: confirming ? "#fff" : "var(--accent-red)",
              background: confirming
                ? "var(--accent-red)"
                : "var(--bg-secondary)",
            }}
          >
            {purging ? (
              <span className="spinner" />
            ) : confirming ? (
              "Confirm purge"
            ) : (
              "Purge safe stale"
            )}
          </button>
        </div>

        {preview ? (
          <div
            className="mobile-kpi-strip"
            aria-label="Stale cleanup summary"
            style={{ marginTop: 12 }}
          >
            {[
              { label: "Stale", value: preview.stale_count },
              { label: "Safe", value: safeRuns.length },
              { label: "Selected", value: purgeTargets.length },
            ].map((item) => (
              <div key={item.label} className="mobile-kpi">
                <div className="mobile-kpi-label">{item.label}</div>
                <div className="mobile-kpi-value">{item.value}</div>
              </div>
            ))}
          </div>
        ) : null}

        {preview ? renderReasonCounts() : null}

        {preview && preview.errors && preview.errors.length > 0 ? (
          <div
            style={{ marginTop: 10, color: "var(--accent-red)", fontSize: 12 }}
          >
            Errors: {preview.errors.join(", ")}
          </div>
        ) : null}

        {preview && runs.length === 0 ? (
          <div
            style={{
              padding: 20,
              color: "var(--text-muted)",
              textAlign: "center",
            }}
          >
            No stale queued runs match these filters
          </div>
        ) : null}

        {preview && runs.length > 0 ? (
          <>
            <div
              className="queue-desktop-table"
              style={{ overflowX: "auto", marginTop: 12 }}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Repo</th>
                    <th>Workflow</th>
                    <th>Branch</th>
                    <th>PR</th>
                    <th>Age</th>
                    <th>Reason</th>
                    <th>Safe</th>
                    <th>Current SHA</th>
                    <th>Run SHA</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>{runs.map((run) => renderRun(run, false))}</tbody>
              </table>
            </div>
            <div
              className="mobile-card-list"
              aria-label="Mobile stale cleanup candidates"
            >
              {runs.map((run) => renderRun(run, true))}
            </div>
          </>
        ) : null}
      </div>
    </Collapse>
  );
}

export default StaleCleanupPanel;
