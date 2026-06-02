/**
 * RemediationIssues.tsx — the "Issues" sub-tab of the Remediation view,
 * extracted (behaviour-wise 1:1) from the legacy `App.tsx` monolith as part of
 * the decomposition epic (#836, pass 8).
 *
 * Self-contained tab: owns its own data fetch (`GET /api/issues`), discovers
 * available sources via `GET /api/linear/workspaces`, persists its filters to
 * localStorage, supports multi-select with pickability/judgement guards, and a
 * force-dispatch confirmation modal that POSTs to `/api/issues/dispatch`. The
 * legacy version read no props; the only ambient App state it touched was the
 * signed-in `principal` (used as the dispatch `approved_by`), now threaded in as
 * an explicit `principalName` prop defaulting to "anonymous".
 */
import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph } from "./decompIcons";
import {
  getComplexityStyle,
  getJudgementStyle,
  getTypeStyle,
  issueKey,
  issueMatchesFilters,
  pillStyle,
  type IssueRecord,
} from "./remediationDispatch";

export type { IssueRecord, IssueTaxonomy } from "./remediationDispatch";

// ── Types ────────────────────────────────────────────────────────────────────

export interface IssueStats {
  unified_total?: number;
  github_total?: number;
  linear_total?: number;
  collapsed?: number;
}

interface SourceOption {
  value: string;
  label: string;
}

interface DispatchResult {
  type: "success" | "error";
  text: string;
}

export interface RemediationIssuesProps {
  /** Name recorded as the dispatch `approved_by`; defaults to "anonymous". */
  principalName?: string;
}

const PROVIDER_OPTIONS = [
  "jules_api",
  "codex_cli",
  "claude_code_cli",
  "gemini_cli",
  "ollama",
  "cline",
];

// ── Component ─────────────────────────────────────────────────────────────────

export function RemediationIssuesSubTab({
  principalName,
}: RemediationIssuesProps = {}): React.ReactElement {
  const [issues, setIssues] = useState<IssueRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState<string>(
    () => localStorage.getItem("issuesSourceFilter") || "",
  );
  const [issueStats, setIssueStats] = useState<IssueStats | null>(null);
  const [sourceOptions, setSourceOptions] = useState<SourceOption[]>([
    { value: "github", label: "GitHub" },
  ]);

  const [repoFilter, setRepoFilter] = useState<string>(
    () => localStorage.getItem("issues:filter_repo") || "",
  );
  const [complexFilter, setComplexFilter] = useState<string>(
    () => localStorage.getItem("issues:filter_complexity") || "",
  );
  const [judgeFilter, setJudgeFilter] = useState<string>(
    () => localStorage.getItem("issues:filter_judgement") || "",
  );
  const [pickableOnly, setPickableOnly] = useState<boolean>(
    () => localStorage.getItem("issues:filter_pickable") === "1",
  );

  const [selected, setSelected] = useState<Record<string, boolean>>({});

  const [dispatchProvider, setDispatchProvider] = useState("jules_api");
  const [dispatchPrompt, setDispatchPrompt] = useState("");

  const [showModal, setShowModal] = useState(false);
  const [forceDispatch, setForceDispatch] = useState(false);
  const [dispatchResult, setDispatchResult] = useState<DispatchResult | null>(
    null,
  );

  const fetchIssues = useCallback(() => {
    const activeSource = sourceFilter || "github";
    setLoading(true);
    setFetchError(null);
    legacyFetch(
      "/api/issues?limit=2000&source=" + encodeURIComponent(activeSource),
    )
      .then((r) => {
        if (!r.ok) {
          throw new Error("HTTP " + r.status);
        }
        return r.json();
      })
      .then((data) => {
        setIssues(Array.isArray(data) ? data : data.items || data.issues || []);
        setIssueStats((data && data.stats) || null);
        setLoading(false);
      })
      .catch((err: Error) => {
        setFetchError(err.message || "Failed to load issues");
        setIssueStats(null);
        setLoading(false);
      });
  }, [sourceFilter]);

  useEffect(() => {
    legacyFetch("/api/linear/workspaces")
      .then((r) => {
        if (!r.ok) {
          throw new Error("HTTP " + r.status);
        }
        return r.json();
      })
      .then((data) => {
        const workspaces = (data && data.workspaces) || [];
        const linearReady = workspaces.some(
          (workspace: { auth_status?: string }) =>
            workspace && workspace.auth_status === "ok",
        );
        const nextOptions: SourceOption[] = linearReady
          ? [
              { value: "github", label: "GitHub" },
              { value: "linear", label: "Linear" },
              { value: "unified", label: "Unified" },
            ]
          : [{ value: "github", label: "GitHub" }];
        const stored = localStorage.getItem("issuesSourceFilter") || "";
        setSourceOptions(nextOptions);
        setSourceFilter((current) => {
          if (
            current &&
            nextOptions.some((option) => option.value === current)
          ) {
            return current;
          }
          if (stored && nextOptions.some((option) => option.value === stored)) {
            return stored;
          }
          return linearReady ? "unified" : "github";
        });
      })
      .catch(() => {
        setSourceOptions([{ value: "github", label: "GitHub" }]);
        setSourceFilter((current) => current || "github");
      });
  }, []);

  useEffect(() => {
    if (sourceFilter) {
      fetchIssues();
    }
  }, [sourceFilter, fetchIssues]);

  useEffect(() => {
    if (sourceFilter) {
      localStorage.setItem("issuesSourceFilter", sourceFilter);
    }
  }, [sourceFilter]);
  useEffect(() => {
    localStorage.setItem("issues:filter_repo", repoFilter);
  }, [repoFilter]);
  useEffect(() => {
    localStorage.setItem("issues:filter_complexity", complexFilter);
  }, [complexFilter]);
  useEffect(() => {
    localStorage.setItem("issues:filter_judgement", judgeFilter);
  }, [judgeFilter]);
  useEffect(() => {
    localStorage.setItem("issues:filter_pickable", pickableOnly ? "1" : "0");
  }, [pickableOnly]);

  const repos = Array.from(
    new Set(
      issues.map((i) => i.repo || i.repository || "").filter(Boolean),
    ),
  ).sort();

  const filtered = issues.filter((issue) =>
    issueMatchesFilters(
      issue,
      repoFilter,
      complexFilter,
      judgeFilter,
      pickableOnly,
    ),
  );

  const selectedItems = filtered.filter((issue) => selected[issueKey(issue)]);
  const selectedCount = selectedItems.length;
  const hasNonPickable = selectedItems.some((i) => !i.pickable);
  const hasDangerous = selectedItems.some((i) => {
    const j = (i.taxonomy || {}).judgement;
    return j === "design" || j === "contested";
  });

  function toggleSelect(issue: IssueRecord): void {
    const key = issueKey(issue);
    setSelected((prev) => {
      const next = { ...prev };
      if (next[key]) {
        delete next[key];
      } else {
        next[key] = true;
      }
      return next;
    });
  }

  function toggleAll(checked: boolean): void {
    if (!checked) {
      setSelected({});
      return;
    }
    const next: Record<string, boolean> = {};
    filtered.forEach((issue) => {
      const repo = issue.repo || issue.repository || "";
      if (issue.pickable !== false && repo && issue.number != null) {
        next[issueKey(issue)] = true;
      }
    });
    setSelected(next);
  }

  function doDispatch(): void {
    const items = selectedItems.map((i) => ({
      repo: i.repo || i.repository || "",
      number: i.number,
    }));
    setDispatchResult(null);
    legacyFetch("/api/issues/dispatch", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        selection: { mode: "list", items },
        provider: dispatchProvider,
        prompt: dispatchPrompt,
        force: forceDispatch,
        confirmation: { approved_by: principalName || "anonymous" },
      }),
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
      .then((result) => {
        if (result.ok) {
          setDispatchResult({
            type: "success",
            text: "Dispatched " + items.length + " issue(s) successfully.",
          });
          setSelected({});
        } else {
          setDispatchResult({
            type: "error",
            text:
              "Dispatch failed: " +
              (result.data.detail || JSON.stringify(result.data)),
          });
        }
        setShowModal(false);
        setForceDispatch(false);
      })
      .catch((err: Error) => {
        setDispatchResult({ type: "error", text: "Dispatch error: " + err.message });
        setShowModal(false);
      });
  }

  return (
    <div style={{ padding: "0 0 16px 0" }}>
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: 12,
          padding: "10px 12px",
          background: "var(--bg-secondary)",
          borderRadius: 8,
          border: "1px solid var(--border)",
        }}
      >
        <select
          value={sourceFilter || "github"}
          onChange={(e) => {
            setSourceFilter(e.target.value);
            setSelected({});
          }}
          style={{
            fontSize: 12,
            padding: "3px 6px",
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        >
          {sourceOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          value={repoFilter}
          onChange={(e) => {
            setRepoFilter(e.target.value);
            setSelected({});
          }}
          style={{
            fontSize: 12,
            padding: "3px 6px",
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        >
          <option value="">All repos</option>
          {repos.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <select
          value={complexFilter}
          onChange={(e) => {
            setComplexFilter(e.target.value);
            setSelected({});
          }}
          style={{
            fontSize: 12,
            padding: "3px 6px",
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        >
          <option value="">All complexity</option>
          {["trivial", "routine", "complex", "deep", "research"].map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          value={judgeFilter}
          onChange={(e) => {
            setJudgeFilter(e.target.value);
            setSelected({});
          }}
          style={{
            fontSize: 12,
            padding: "3px 6px",
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        >
          <option value="">All judgement</option>
          {["objective", "preference", "design", "contested"].map((j) => (
            <option key={j} value={j}>
              {j}
            </option>
          ))}
        </select>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: 12,
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          <input
            type="checkbox"
            checked={pickableOnly}
            onChange={(e) => {
              setPickableOnly(e.target.checked);
              setSelected({});
            }}
          />
          Pickable only
        </label>
        <button
          className="btn"
          onClick={fetchIssues}
          style={{ marginLeft: "auto" }}
        >
          <RefreshGlyph size={12} />
          Refresh
        </button>
      </div>

      {dispatchResult ? (
        <div
          style={{
            marginBottom: 10,
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: 12,
            background:
              dispatchResult.type === "success"
                ? "rgba(63,185,80,0.12)"
                : "rgba(248,81,73,0.12)",
            color:
              dispatchResult.type === "success"
                ? "var(--accent-green)"
                : "var(--accent-red)",
          }}
        >
          {dispatchResult.text}
        </div>
      ) : null}

      {sourceFilter === "unified" && issueStats ? (
        <div
          style={{
            marginBottom: 10,
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: 12,
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          {(issueStats.unified_total || filtered.length) +
            " issues - " +
            (issueStats.github_total || 0) +
            " GitHub, " +
            (issueStats.linear_total || 0) +
            " Linear, " +
            (issueStats.collapsed || 0) +
            " collapsed"}
        </div>
      ) : null}

      {loading ? (
        <div
          style={{
            color: "var(--text-muted)",
            fontSize: 12,
            padding: "12px 0",
          }}
        >
          Loading issues...
        </div>
      ) : null}
      {fetchError ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
            fontSize: 12,
          }}
        >
          {fetchError}
        </div>
      ) : null}

      {!loading && !fetchError ? (
        <div style={{ overflowX: "auto" }}>
          {filtered.length === 0 ? (
            <div
              style={{
                color: "var(--text-muted)",
                fontSize: 13,
                padding: "24px 0",
                textAlign: "center",
              }}
            >
              No issues match the current filters.
            </div>
          ) : (
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12,
              }}
            >
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <th style={{ padding: "6px 8px", textAlign: "center", width: 28 }}>
                    <input
                      type="checkbox"
                      onChange={(e) => toggleAll(e.target.checked)}
                      checked={
                        filtered.length > 0 &&
                        filtered
                          .filter((i) => i.pickable !== false)
                          .every((i) => {
                            const repo = i.repo || i.repository || "";
                            return !repo || i.number == null
                              ? true
                              : selected[issueKey(i)];
                          })
                      }
                    />
                  </th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>Repo</th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>#</th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>Title</th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>Type</th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>
                    Complexity
                  </th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>Effort</th>
                  <th style={{ padding: "6px 8px", textAlign: "left" }}>
                    Judgement
                  </th>
                  <th style={{ padding: "6px 8px", textAlign: "center" }}>
                    Pickable
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((issue) => {
                  const taxonomy = issue.taxonomy || {};
                  const repo = issue.repo || issue.repository || "";
                  const key = issueKey(issue);
                  const isSelected = !!selected[key];
                  const pickable = issue.pickable !== false;
                  const dispatchable = !!repo && issue.number != null;
                  const selectable = pickable && dispatchable;
                  const blockedBy = issue.pickable_blocked_by || [];
                  const title = issue.title || "";
                  const truncTitle =
                    title.length > 80 ? title.slice(0, 80) + "…" : title;
                  const issueUrl =
                    issue.url ||
                    issue.html_url ||
                    (repo && issue.number != null
                      ? "https://github.com/" + repo + "/issues/" + issue.number
                      : "#");
                  const repoUrl = "https://github.com/" + repo;
                  const typeStyle = getTypeStyle(
                    taxonomy.type || taxonomy.issue_type,
                  );
                  const complexityStyle = getComplexityStyle(taxonomy.complexity);
                  const judgementStyle = getJudgementStyle(taxonomy.judgement);
                  const isDangerous =
                    taxonomy.judgement === "design" ||
                    taxonomy.judgement === "contested";
                  const sources =
                    Array.isArray(issue.sources) && issue.sources.length
                      ? issue.sources
                      : ["github"];
                  const linearId =
                    issue.linear && issue.linear.identifier
                      ? issue.linear.identifier
                      : "";
                  const linearUrl =
                    issue.linear && issue.linear.url ? issue.linear.url : "";
                  return (
                    <tr
                      key={key}
                      style={{
                        borderBottom: "1px solid var(--border)",
                        opacity: selectable ? 1 : 0.7,
                        background: selectable
                          ? "transparent"
                          : "rgba(255,0,0,0.04)",
                      }}
                    >
                      <td style={{ padding: "6px 8px", textAlign: "center" }}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          disabled={!selectable}
                          title={
                            !dispatchable
                              ? "Linear-only items cannot be dispatched until linked to a GitHub issue."
                              : !pickable && blockedBy.length
                                ? blockedBy.join(", ")
                                : undefined
                          }
                          style={
                            selectable
                              ? {}
                              : { cursor: "not-allowed", opacity: 0.5 }
                          }
                          onChange={() => {
                            if (selectable) toggleSelect(issue);
                          }}
                        />
                      </td>
                      <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>
                        {repo ? (
                          <a
                            href={repoUrl}
                            target="_blank"
                            rel="noreferrer"
                            style={{
                              color: "var(--text-secondary)",
                              textDecoration: "none",
                            }}
                          >
                            {repo}
                          </a>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>
                            {linearId || "Linear-only"}
                          </span>
                        )}
                      </td>
                      <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>
                        {issue.number != null ? (
                          <a
                            href={issueUrl}
                            target="_blank"
                            rel="noreferrer"
                            style={{
                              color: "var(--accent-blue)",
                              textDecoration: "none",
                            }}
                          >
                            {"#" + issue.number}
                          </a>
                        ) : (
                          <a
                            href={linearUrl || issueUrl}
                            target="_blank"
                            rel="noreferrer"
                            style={{
                              color: "var(--accent-blue)",
                              textDecoration: "none",
                            }}
                          >
                            {linearId || "Linear"}
                          </a>
                        )}
                      </td>
                      <td style={{ padding: "6px 8px", maxWidth: 300 }}>
                        <div
                          style={{
                            display: "flex",
                            gap: 6,
                            flexWrap: "wrap",
                            alignItems: "center",
                            marginBottom: 4,
                          }}
                        >
                          {sources.map((src) => (
                            <span
                              key={src}
                              style={pillStyle(
                                src === "linear"
                                  ? {
                                      background: "rgba(99,102,241,0.18)",
                                      color: "var(--accent-purple)",
                                    }
                                  : {
                                      background: "rgba(88,166,255,0.18)",
                                      color: "var(--accent-blue)",
                                    },
                              )}
                            >
                              {src.toUpperCase()}
                            </span>
                          ))}
                          {linearId ? (
                            <a
                              href={linearUrl || issueUrl}
                              target="_blank"
                              rel="noreferrer"
                              style={{
                                color: "var(--accent-purple)",
                                textDecoration: "none",
                                fontWeight: 600,
                              }}
                            >
                              {linearId}
                            </a>
                          ) : null}
                        </div>
                        {taxonomy.quick_win ? (
                          <span
                            style={{
                              color: "var(--accent-yellow)",
                              marginRight: 4,
                            }}
                          >
                            ★
                          </span>
                        ) : null}
                        <span title={title}>{truncTitle}</span>
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        {taxonomy.type || taxonomy.issue_type ? (
                          <span style={pillStyle(typeStyle)}>
                            {taxonomy.type || taxonomy.issue_type}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        {taxonomy.complexity ? (
                          <span style={pillStyle(complexityStyle)}>
                            {taxonomy.complexity}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>
                        {taxonomy.effort || "—"}
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        {taxonomy.judgement ? (
                          <span style={pillStyle(judgementStyle)}>
                            {isDangerous ? "🛑 " : ""}
                            {taxonomy.judgement}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "center" }}>
                        {selectable ? (
                          <span
                            style={{ color: "var(--accent-green)", fontSize: 14 }}
                          >
                            ✓
                          </span>
                        ) : (
                          <span
                            title={
                              !dispatchable
                                ? "Linear-only items cannot be dispatched until linked to a GitHub issue."
                                : blockedBy.join(", ")
                            }
                            style={{
                              color: "var(--accent-red)",
                              fontSize: 14,
                              cursor: "help",
                            }}
                          >
                            ✗
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      ) : null}

      {selectedCount > 0 ? (
        <div
          style={{
            marginTop: 16,
            padding: "12px 16px",
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            display: "flex",
            gap: 12,
            alignItems: "flex-start",
            flexWrap: "wrap",
          }}
        >
          <div
            style={{
              fontSize: 12,
              color: "var(--text-secondary)",
              alignSelf: "center",
            }}
          >
            {selectedCount +
              " issue" +
              (selectedCount !== 1 ? "s" : "") +
              " selected"}
          </div>
          <select
            value={dispatchProvider}
            onChange={(e) => setDispatchProvider(e.target.value)}
            style={{
              fontSize: 12,
              padding: "4px 8px",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: 4,
            }}
          >
            {PROVIDER_OPTIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <textarea
            value={dispatchPrompt}
            onChange={(e) => setDispatchPrompt(e.target.value)}
            placeholder="Optional prompt / instructions for agent…"
            rows={2}
            style={{
              flex: "1 1 200px",
              fontSize: 12,
              padding: "4px 8px",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              resize: "vertical",
              minWidth: 150,
            }}
          />
          <button
            className="btn btn-primary"
            onClick={() => {
              setShowModal(true);
              setForceDispatch(false);
            }}
          >
            Dispatch to selected
          </button>
        </div>
      ) : null}

      {showModal ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setShowModal(false);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setShowModal(false);
            }
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-dispatch-title"
            style={{
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 24,
              maxWidth: 540,
              width: "90vw",
              maxHeight: "80vh",
              overflowY: "auto",
            }}
          >
            <h3
              id="confirm-dispatch-title"
              style={{ margin: "0 0 12px 0", fontSize: 15 }}
            >
              Confirm Dispatch
            </h3>

            {hasDangerous ? (
              <div
                style={{
                  marginBottom: 12,
                  padding: "8px 12px",
                  borderRadius: 6,
                  background: "rgba(220,38,38,0.15)",
                  color: "var(--accent-red)",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                🛑 Warning: one or more selected issues have judgement:design or
                judgement:contested. These require panel review and should not be
                auto-dispatched.
              </div>
            ) : null}

            <div
              style={{
                fontSize: 12,
                marginBottom: 10,
                color: "var(--text-secondary)",
              }}
            >
              {"Dispatching " +
                selectedCount +
                " issue" +
                (selectedCount !== 1 ? "s" : "") +
                " to provider: "}
              <strong>{dispatchProvider}</strong>
            </div>

            <div style={{ maxHeight: 200, overflowY: "auto", marginBottom: 12 }}>
              {selectedItems.map((issue) => {
                const repo = issue.repo || issue.repository || "";
                return (
                  <div
                    key={issue.number + ":" + repo}
                    style={{
                      padding: "4px 8px",
                      fontSize: 12,
                      borderBottom: "1px solid var(--border)",
                      opacity: issue.pickable === false ? 0.6 : 1,
                    }}
                  >
                    <span style={{ color: "var(--text-muted)" }}>
                      {repo + " "}
                    </span>
                    <strong>{"#" + issue.number}</strong>
                    {" — "}
                    <span>{(issue.title || "").slice(0, 80)}</span>
                    {issue.pickable === false ? (
                      <span
                        style={{ color: "var(--accent-red)", marginLeft: 6 }}
                      >
                        (non-pickable)
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>

            {hasNonPickable ? (
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 12,
                  marginBottom: 12,
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={forceDispatch}
                  onChange={(e) => setForceDispatch(e.target.checked)}
                />
                <span style={{ color: "var(--accent-red)", fontWeight: 600 }}>
                  Force dispatch (include non-pickable issues)
                </span>
              </label>
            ) : null}

            <div
              style={{
                display: "flex",
                gap: 8,
                justifyContent: "flex-end",
                marginTop: 16,
              }}
            >
              <button className="btn" onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={doDispatch}>
                Confirm Dispatch
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default RemediationIssuesSubTab;
