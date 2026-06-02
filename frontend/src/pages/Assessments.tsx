/**
 * Assessments.tsx — the "Assessments" tab, extracted (behaviour-wise 1:1) from
 * the legacy `App.tsx` monolith as part of the decomposition epic (#836, pass
 * 4).
 *
 * Lets an operator dispatch a repository assessment (Jules / Codex / Claude)
 * and browses the parsed scores found under the `assessments/` directory,
 * grouped per repository, with both a mobile card layout and a desktop table.
 *
 * Presentational: the repo list, score list, and their poll are owned by the
 * legacy App (the same `repos`/`scores` state feeds several other tabs), so to
 * stay DRY and avoid double-polling this page receives the already-fetched
 * `repos`/`scores`, a `loading` flag, an optional `error`, and `onDispatch` /
 * `onRefresh` callbacks. Loading/empty states and a11y semantics mirror the
 * original legacy render exactly.
 */
import React, { useState } from "react";
import { ActivityGlyph, RefreshGlyph } from "./decompIcons";

// ── Types ──────────────────────────────────────────────────────────────────

/** A repository (only `name` is consumed; the legacy code also accepts a bare
 * string, so the select renders `r.name || r`). */
export interface AssessmentRepo {
  name?: string;
}

/** A single parsed assessment score entry. */
export interface AssessmentScore {
  repo?: string | null;
  score?: number | string | null;
  date?: number | string | null;
  provider?: string | null;
  summary?: string | null;
}

/** The dispatch payload sent to the backend. */
export interface AssessmentDispatch {
  repository: string;
  provider: string;
}

export interface AssessmentsProps {
  /** All organization repositories (owned/polled by the legacy App). */
  repos: (AssessmentRepo | string)[];
  /** Parsed assessment scores. */
  scores: AssessmentScore[];
  /** True while scores are being (re)fetched. */
  loading?: boolean;
  /** Error banner text, if any. */
  error?: string | null;
  /** Dispatch an assessment run; resolves on success. */
  onDispatch: (payload: AssessmentDispatch) => Promise<unknown>;
  /** Refresh the scores list. */
  onRefresh: () => void;
}

type DispatchStatus = null | "dispatching" | "ok" | "error";

// ── Helpers (ported 1:1 from the legacy App) ─────────────────────────────────

function repoName(r: AssessmentRepo | string): string {
  return typeof r === "string" ? r : r.name || "";
}

function formatAssessmentScore(e: AssessmentScore): string | number {
  const value = e && e.score;
  if (value == null) return "—";
  if (typeof value === "number") {
    return value <= 1 ? Math.round(value * 100) + "%" : value;
  }
  return value;
}

function formatAssessmentDate(e: AssessmentScore): string {
  const value = e && e.date;
  if (!value) return "—";
  if (typeof value === "number") {
    return new Date(value * 1000).toLocaleDateString();
  }
  return String(value).slice(0, 10);
}

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "4px 8px",
  borderBottom: "1px solid var(--border)",
  color: "var(--text-muted)",
};

// ── Page ─────────────────────────────────────────────────────────────────────

export function AssessmentsTab({
  repos,
  scores,
  loading,
  error,
  onDispatch,
  onRefresh,
}: AssessmentsProps): React.ReactElement {
  const [selRepo, setSelRepo] = useState("");
  const [selProvider, setSelProvider] = useState("jules_api");
  const [showConfirm, setShowConfirm] = useState(false);
  const [dispatchStatus, setDispatchStatus] = useState<DispatchStatus>(null);

  const grouped: Record<string, AssessmentScore[]> = {};
  scores.forEach((s) => {
    const k = s.repo || "unknown";
    if (!grouped[k]) grouped[k] = [];
    grouped[k].push(s);
  });

  function doDispatch(): void {
    setShowConfirm(false);
    setDispatchStatus("dispatching");
    onDispatch({ repository: selRepo, provider: selProvider })
      .then(() => {
        setDispatchStatus("ok");
      })
      .catch(() => {
        setDispatchStatus("error");
      });
  }

  return (
    <div style={{ padding: 20 }}>
      <div className="section-header">
        <ActivityGlyph size={14} />
        Assessments
      </div>
      {error ? <div className="error-banner">{error}</div> : null}
      <div
        style={{
          display: "flex",
          gap: 10,
          marginBottom: 16,
          flexWrap: "wrap",
          alignItems: "flex-end",
        }}
      >
        <div>
          <label
            style={{
              display: "block",
              fontSize: 11,
              color: "var(--text-muted)",
              marginBottom: 4,
            }}
          >
            Repository
          </label>
          <select
            value={selRepo}
            onChange={(e) => setSelRepo(e.target.value)}
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              borderRadius: 4,
              padding: "4px 8px",
              minWidth: 180,
            }}
          >
            <option value="">— pick a repo —</option>
            {repos.map((r) => {
              const name = repoName(r);
              return (
                <option key={name} value={name}>
                  {name}
                </option>
              );
            })}
          </select>
        </div>
        <div>
          <label
            style={{
              display: "block",
              fontSize: 11,
              color: "var(--text-muted)",
              marginBottom: 4,
            }}
          >
            Provider
          </label>
          <select
            value={selProvider}
            onChange={(e) => setSelProvider(e.target.value)}
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              borderRadius: 4,
              padding: "4px 8px",
            }}
          >
            <option value="jules_api">Jules</option>
            <option value="codex">Codex</option>
            <option value="claude">Claude</option>
          </select>
        </div>
        <button
          className="action-btn"
          disabled={!selRepo}
          onClick={() => setShowConfirm(true)}
        >
          <ActivityGlyph size={14} /> Run Assessment
        </button>
        <button
          className="action-btn secondary"
          onClick={onRefresh}
          disabled={loading}
        >
          <RefreshGlyph size={12} /> Refresh
        </button>
      </div>
      {dispatchStatus === "ok" ? (
        <div
          style={{
            color: "var(--accent-green)",
            marginBottom: 12,
            fontSize: 13,
          }}
        >
          Assessment dispatched successfully.
        </div>
      ) : null}
      {dispatchStatus === "error" ? (
        <div
          style={{
            color: "var(--accent-red)",
            marginBottom: 12,
            fontSize: 13,
          }}
        >
          Dispatch failed — check GitHub Actions.
        </div>
      ) : null}
      {showConfirm ? (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: 16,
            marginBottom: 16,
          }}
        >
          <p style={{ margin: "0 0 12px", fontSize: 13 }}>
            Dispatch assessment of <strong>{selRepo}</strong> via{" "}
            <strong>{selProvider}</strong>?
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="action-btn" onClick={doDispatch}>
              Confirm
            </button>
            <button
              className="action-btn secondary"
              onClick={() => setShowConfirm(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
      {loading ? (
        <div style={{ color: "var(--text-muted)", padding: 20 }}>
          Loading scores…
        </div>
      ) : scores.length === 0 ? (
        <div style={{ color: "var(--text-muted)", padding: 20 }}>
          No assessment scores found in assessments/ directory.
        </div>
      ) : (
        <div>
          {Object.keys(grouped)
            .sort()
            .map((repoKey) => {
              const entries = grouped[repoKey];
              return (
                <div key={repoKey} style={{ marginBottom: 20 }}>
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: 13,
                      marginBottom: 8,
                      color: "var(--text-primary)",
                    }}
                  >
                    {repoKey}
                  </div>
                  <div className="assessment-mobile-card-list">
                    {entries.map((e, i) => (
                      <article
                        key={"mobile-" + i}
                        className="assessment-mobile-card"
                      >
                        <div className="assessment-mobile-card-title">
                          <span>{repoKey}</span>
                          <span className="assessment-mobile-score">
                            {formatAssessmentScore(e)}
                          </span>
                        </div>
                        <div className="assessment-mobile-summary">
                          {e.summary || "No summary captured."}
                        </div>
                        <div className="assessment-mobile-meta">
                          <span className="assessment-mobile-chip">
                            {e.provider || "provider unknown"}
                          </span>
                          <span className="assessment-mobile-chip">
                            {formatAssessmentDate(e)}
                          </span>
                        </div>
                      </article>
                    ))}
                  </div>
                  <table
                    className="assessment-desktop-table"
                    style={{
                      width: "100%",
                      borderCollapse: "collapse",
                      fontSize: 12,
                    }}
                  >
                    <thead>
                      <tr>
                        <th style={thStyle}>Score</th>
                        <th style={thStyle}>Provider</th>
                        <th style={thStyle}>Date</th>
                        <th style={thStyle}>Summary</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((e, i) => {
                        const score = formatAssessmentScore(e);
                        const dateStr = formatAssessmentDate(e);
                        return (
                          <tr
                            key={i}
                            style={{ borderBottom: "1px solid var(--border)" }}
                          >
                            <td
                              style={{
                                padding: "4px 8px",
                                fontWeight: 600,
                                color: "var(--accent-green)",
                              }}
                            >
                              {score}
                            </td>
                            <td
                              style={{
                                padding: "4px 8px",
                                color: "var(--text-secondary)",
                              }}
                            >
                              {e.provider || "—"}
                            </td>
                            <td
                              style={{
                                padding: "4px 8px",
                                color: "var(--text-muted)",
                              }}
                            >
                              {dateStr}
                            </td>
                            <td
                              style={{
                                padding: "4px 8px",
                                color: "var(--text-secondary)",
                                maxWidth: 300,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {e.summary || "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}

export default AssessmentsTab;
