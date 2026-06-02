/**
 * Workflows.tsx — the "Workflows" tab, extracted (behaviour-wise 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 4).
 *
 * Lists every workflow across the organization, grouped by repository, with
 * search / repo / trigger filters (persisted to sessionStorage exactly as the
 * legacy code did), per-workflow expand-to-see-recent-runs, manual dispatch
 * with a two-step confirm modal, and external links to the workflow / runs.
 *
 * Presentational: the workflow list (and its poll) is owned by the legacy App,
 * so this page receives the already-fetched `workflows`, a `loading` flag, an
 * optional `error`, and `onDispatch` / `onRefresh` callbacks. Filter state,
 * expand state, and the dispatch modal are local. Loading/empty states and
 * a11y semantics mirror the original legacy render exactly.
 */
import React, { useEffect, useState } from "react";
import { RefreshGlyph, ServerGlyph } from "./decompIcons";

// ── Types ──────────────────────────────────────────────────────────────────

interface WorkflowRun {
  id?: number | string;
  status?: string | null;
  conclusion?: string | null;
  created_at?: string | null;
  html_url?: string | null;
}

export interface Workflow {
  id: number | string;
  name: string;
  repository: string;
  triggers?: string[];
  html_url?: string | null;
  latest_run?: WorkflowRun | null;
  recent_runs?: WorkflowRun[];
}

/** Dispatch payload sent to the backend. */
export interface WorkflowDispatch {
  repository: string;
  workflow_id: number | string;
  ref: string;
  inputs: Record<string, unknown>;
}

export interface WorkflowsProps {
  workflows: Workflow[];
  loading?: boolean;
  error?: string | null;
  onDispatch: (payload: WorkflowDispatch) => Promise<unknown>;
  onRefresh: () => void;
}

interface DispatchModalState {
  wf?: Workflow;
  ref?: string;
  inputs?: Record<string, unknown>;
}

interface SavedFilters {
  search?: string;
  repo?: string;
  trigger?: string;
}

// ── Helpers (ported 1:1 from the legacy App) ─────────────────────────────────

const TRIGGER_COLORS: Record<string, { bg: string; color: string }> = {
  manual: { bg: "rgba(88,166,255,0.15)", color: "var(--accent-blue, #58a6ff)" },
  schedule: { bg: "rgba(136,108,228,0.15)", color: "var(--accent-purple)" },
  push_pr: { bg: "rgba(63,185,80,0.15)", color: "var(--accent-green)" },
  workflow_run: { bg: "rgba(210,153,34,0.15)", color: "var(--accent-yellow)" },
};

function triggerBadge(trigger: string): React.ReactElement {
  const c = TRIGGER_COLORS[trigger] || {
    bg: "rgba(139,148,158,0.15)",
    color: "var(--text-muted)",
  };
  return (
    <span
      key={trigger}
      className="section-badge"
      style={{ background: c.bg, color: c.color, marginRight: 4 }}
    >
      {trigger}
    </span>
  );
}

function conclusionColor(conclusion?: string | null): string {
  if (conclusion === "success") return "var(--accent-green)";
  if (conclusion === "failure") return "var(--accent-red)";
  if (conclusion === "cancelled") return "var(--text-muted)";
  return "var(--accent-yellow)";
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function WorkflowsTab({
  workflows,
  loading,
  error,
  onDispatch,
  onRefresh,
}: WorkflowsProps): React.ReactElement {
  let savedFilters: SavedFilters = {};
  try {
    savedFilters = JSON.parse(
      sessionStorage.getItem("workflowsMobileFilters") || "{}",
    );
  } catch {
    savedFilters = {};
  }

  const [searchFilter, setSearchFilter] = useState(savedFilters.search || "");
  const [repoFilter, setRepoFilter] = useState(savedFilters.repo || "all");
  const [triggerFilter, setTriggerFilter] = useState(
    savedFilters.trigger || "all",
  );
  const [expandedId, setExpandedId] = useState<number | string | null>(null);
  const [dispatchingWf, setDispatchingWf] = useState<number | string | null>(
    null,
  );
  const [dispatchModal, setDispatchModal] = useState<DispatchModalState>({});
  const [dispatchConfirm, setDispatchConfirm] = useState(false);

  useEffect(() => {
    sessionStorage.setItem(
      "workflowsMobileFilters",
      JSON.stringify({
        search: searchFilter,
        repo: repoFilter,
        trigger: triggerFilter,
      }),
    );
  }, [searchFilter, repoFilter, triggerFilter]);

  const repos = Array.from(new Set(workflows.map((w) => w.repository))).sort();

  const filtered = workflows.filter((w) => {
    if (repoFilter !== "all" && w.repository !== repoFilter) return false;
    if (triggerFilter !== "all" && !(w.triggers || []).includes(triggerFilter))
      return false;
    if (searchFilter) {
      const q = searchFilter.toLowerCase();
      if (
        !w.name.toLowerCase().includes(q) &&
        !w.repository.toLowerCase().includes(q)
      )
        return false;
    }
    return true;
  });

  const byRepo: Record<string, Workflow[]> = {};
  filtered.forEach((w) => {
    if (!byRepo[w.repository]) byRepo[w.repository] = [];
    byRepo[w.repository].push(w);
  });

  function openDispatch(wf: Workflow): void {
    setDispatchModal({ wf, ref: "main", inputs: {} });
    setDispatchConfirm(false);
  }

  function doDispatch(): void {
    const wf = dispatchModal.wf;
    if (!wf) return;
    setDispatchingWf(wf.id);
    onDispatch({
      repository: wf.repository,
      workflow_id: wf.id,
      ref: dispatchModal.ref || "main",
      inputs: dispatchModal.inputs || {},
    }).finally(() => {
      setDispatchingWf(null);
      setDispatchModal({});
      setDispatchConfirm(false);
    });
  }

  return (
    <div>
      <div
        className="mobile-workflow-filters"
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 16,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <input
          type="text"
          placeholder="Search workflows or repos…"
          value={searchFilter}
          onChange={(e) => setSearchFilter(e.target.value)}
          style={{
            flex: "1 1 200px",
            minWidth: 180,
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "7px 10px",
            fontSize: 13,
          }}
        />
        <select
          value={repoFilter}
          onChange={(e) => setRepoFilter(e.target.value)}
          style={{
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "7px 10px",
            fontSize: 13,
          }}
        >
          <option value="all">All repos</option>
          {repos.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <select
          value={triggerFilter}
          onChange={(e) => setTriggerFilter(e.target.value)}
          style={{
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "7px 10px",
            fontSize: 13,
          }}
        >
          <option value="all">All triggers</option>
          <option value="manual">Manual dispatch</option>
          <option value="schedule">Scheduled</option>
          <option value="push_pr">Push/PR</option>
          <option value="workflow_run">Workflow run</option>
        </select>
        <button className="btn" onClick={onRefresh} disabled={loading}>
          <RefreshGlyph size={12} />
          {loading ? "Loading…" : "Refresh"}
        </button>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {filtered.length + " workflows"}
        </span>
      </div>
      {loading && !workflows.length ? (
        <div
          style={{
            color: "var(--text-muted)",
            textAlign: "center",
            padding: 32,
          }}
        >
          Loading workflows…
        </div>
      ) : null}
      {error ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
            fontSize: 12,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      ) : null}
      {Object.keys(byRepo)
        .sort()
        .map((repoName) => {
          const repoWfs = byRepo[repoName];
          return (
            <div
              key={repoName}
              className="section"
              style={{ marginBottom: 12 }}
            >
              <div className="section-header">
                <span className="section-title">
                  <ServerGlyph size={14} />
                  {repoName}
                </span>
                <span className="section-badge">
                  {repoWfs.length + " workflows"}
                </span>
              </div>
              <div className="section-body" style={{ padding: 0 }}>
                {repoWfs.map((wf) => {
                  const expanded = expandedId === wf.id;
                  const lr = wf.latest_run;
                  return (
                    <div
                      key={wf.id}
                      style={{
                        padding: "10px 14px",
                        borderBottom: "1px solid var(--border)",
                        cursor: "pointer",
                      }}
                      onClick={() => setExpandedId(expanded ? null : wf.id)}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          flexWrap: "wrap",
                        }}
                      >
                        <span
                          style={{
                            fontWeight: 600,
                            fontSize: 13,
                            flex: 1,
                            minWidth: 0,
                          }}
                        >
                          {wf.name}
                        </span>
                        {(wf.triggers || []).map(triggerBadge)}
                        {lr ? (
                          <span
                            style={{
                              fontSize: 12,
                              color: conclusionColor(lr.conclusion),
                              fontWeight: 500,
                            }}
                          >
                            {lr.conclusion || lr.status || "?"}
                          </span>
                        ) : null}
                        {wf.triggers && wf.triggers.includes("manual") ? (
                          <button
                            className="btn"
                            style={{ fontSize: 11, padding: "3px 10px" }}
                            disabled={dispatchingWf === wf.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              openDispatch(wf);
                            }}
                          >
                            {dispatchingWf === wf.id ? "Dispatching…" : "Run"}
                          </button>
                        ) : null}
                        {wf.html_url ? (
                          <a
                            href={wf.html_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ fontSize: 11, color: "var(--text-muted)" }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            ↗
                          </a>
                        ) : null}
                      </div>
                      {expanded ? (
                        <div style={{ marginTop: 8, fontSize: 12 }}>
                          <div
                            style={{
                              color: "var(--text-secondary)",
                              marginBottom: 6,
                            }}
                          >
                            Recent runs:
                          </div>
                          {(wf.recent_runs || []).length === 0 ? (
                            <span style={{ color: "var(--text-muted)" }}>
                              No recent runs
                            </span>
                          ) : (
                            (wf.recent_runs || []).map((r) => (
                              <div
                                key={r.id}
                                style={{
                                  display: "flex",
                                  gap: 8,
                                  alignItems: "center",
                                  marginBottom: 4,
                                }}
                              >
                                <span
                                  style={{
                                    color: conclusionColor(r.conclusion),
                                    minWidth: 60,
                                  }}
                                >
                                  {r.conclusion || r.status || "?"}
                                </span>
                                <span style={{ color: "var(--text-muted)" }}>
                                  {r.created_at ? r.created_at.slice(0, 10) : ""}
                                </span>
                                {r.html_url ? (
                                  <a
                                    href={r.html_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{ color: "var(--text-secondary)" }}
                                  >
                                    {"#" + r.id}
                                  </a>
                                ) : null}
                              </div>
                            ))
                          )}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      {dispatchModal && dispatchModal.wf ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              padding: 24,
              minWidth: 360,
              maxWidth: 480,
            }}
          >
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
              {"Dispatch: " + dispatchModal.wf.name}
            </div>
            <div
              style={{
                fontSize: 12,
                color: "var(--text-secondary)",
                marginBottom: 8,
              }}
            >
              {"Repository: " + dispatchModal.wf.repository}
            </div>
            <label
              style={{
                fontSize: 12,
                color: "var(--text-secondary)",
                display: "block",
                marginBottom: 6,
              }}
            >
              Ref (branch/tag):
              <input
                type="text"
                value={dispatchModal.ref || "main"}
                onChange={(e) =>
                  setDispatchModal((prev) =>
                    Object.assign({}, prev, { ref: e.target.value }),
                  )
                }
                style={{
                  display: "block",
                  width: "100%",
                  marginTop: 4,
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "6px 10px",
                  boxSizing: "border-box",
                }}
              />
            </label>
            {!dispatchConfirm ? (
              <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                <button
                  className="btn"
                  onClick={() => setDispatchConfirm(true)}
                >
                  Confirm dispatch
                </button>
                <button
                  className="btn"
                  style={{ opacity: 0.7 }}
                  onClick={() => setDispatchModal({})}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--accent-yellow)",
                    marginBottom: 10,
                    padding: "8px 10px",
                    background: "rgba(210,153,34,0.1)",
                    borderRadius: 6,
                  }}
                >
                  {"This will trigger " +
                    dispatchModal.wf.name +
                    " on " +
                    dispatchModal.wf.repository +
                    " at ref " +
                    (dispatchModal.ref || "main") +
                    "."}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    className="btn"
                    onClick={doDispatch}
                    disabled={!!dispatchingWf}
                  >
                    {dispatchingWf ? "Dispatching…" : "Dispatch now"}
                  </button>
                  <button
                    className="btn"
                    style={{ opacity: 0.7 }}
                    onClick={() => {
                      setDispatchModal({});
                      setDispatchConfirm(false);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default WorkflowsTab;
