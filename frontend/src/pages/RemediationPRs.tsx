/**
 * RemediationPRs.tsx — the "PRs" sub-tab of the Remediation view, extracted
 * (behaviour-wise 1:1) from the legacy `App.tsx` monolith as part of the
 * decomposition epic (#836, pass 8).
 *
 * Self-contained tab: owns its own data fetch (`GET /api/prs`), repo/author/
 * draft filtering, column sorting, multi-select, and a bulk-dispatch modal that
 * POSTs to `/api/prs/dispatch`. The legacy version read no props; the only piece
 * of ambient App state it touched was the signed-in `principal` (used as the
 * dispatch `approved_by`), now threaded in as an explicit `principalName` prop
 * defaulting to "anonymous" — preserving the original fallback exactly.
 */
import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph } from "./decompIcons";
import { sortRows, type SortState } from "./decompSort";
import { SortTh } from "./decompSortTh";
import {
  prAgeHours,
  prAgeLabel,
  prMatchesFilters,
  prRowId,
  type PullRequest,
} from "./remediationDispatch";

export type { PullRequest } from "./remediationDispatch";

// ── Types ────────────────────────────────────────────────────────────────────

interface DispatchMsg {
  type: "success" | "error";
  text: string;
}

interface DispatchModalState {
  items: PullRequest[];
  mode: "selected" | "all";
}

export interface RemediationPRsProps {
  /** Name recorded as the dispatch `approved_by`; defaults to "anonymous". */
  principalName?: string;
}

const PROVIDERS: ReadonlyArray<readonly [string, string]> = [
  ["jules_api", "Jules API"],
  ["codex_cli", "Codex CLI"],
  ["claude_code_cli", "Claude Code CLI"],
  ["gemini_cli", "Gemini CLI"],
  ["ollama", "Ollama"],
  ["cline", "Cline"],
];

const sortAccessors = {
  repo: (pr: PullRequest) => pr.repo || pr.repository || pr.full_name || "",
  number: (pr: PullRequest) => pr.number || pr.pr_number || 0,
  title: (pr: PullRequest) => pr.title || "",
  author: (pr: PullRequest) => pr.author || (pr.user && pr.user.login) || "",
  age: (pr: PullRequest) => prAgeHours(pr) ?? 0,
};

// ── Component ─────────────────────────────────────────────────────────────────

export function RemediationPRsSubTab({
  principalName,
}: RemediationPRsProps = {}): React.ReactElement {
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [repoFilter, setRepoFilter] = useState("");
  const [authorFilter, setAuthorFilter] = useState("");
  const [showDrafts, setShowDrafts] = useState(true);

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [sort, setSort] = useState<SortState>({ key: "age", dir: "asc" });

  const [dispatchModal, setDispatchModal] = useState<DispatchModalState | null>(
    null,
  );
  const [dispatching, setDispatching] = useState(false);
  const [dispatchMsg, setDispatchMsg] = useState<DispatchMsg | null>(null);

  const [modalProvider, setModalProvider] = useState("jules_api");
  const [modalPrompt, setModalPrompt] = useState("");

  const fetchPRs = useCallback(() => {
    setLoading(true);
    setFetchError(null);
    legacyFetch("/api/prs?limit=2000")
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((data) => {
        const list: PullRequest[] = Array.isArray(data)
          ? data
          : data.prs || data.items || [];
        setPrs(list);
        setSelected({});
      })
      .catch((err: Error) => {
        setFetchError("Failed to load PRs: " + err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    fetchPRs();
  }, [fetchPRs]);

  const filtered = prs.filter((pr) =>
    prMatchesFilters(pr, repoFilter, authorFilter, showDrafts),
  );
  const sortedPRs = sortRows(filtered, sort, sortAccessors);

  const visibleIds = sortedPRs.map(prRowId);
  const selectedIds = Object.keys(selected).filter((id) => selected[id]);
  const allVisible =
    visibleIds.length > 0 && visibleIds.every((id) => selected[id]);

  function toggleAll(): void {
    const next = { ...selected };
    if (allVisible) {
      visibleIds.forEach((id) => {
        delete next[id];
      });
    } else {
      visibleIds.forEach((id) => {
        next[id] = true;
      });
    }
    setSelected(next);
  }

  function toggleRow(id: string): void {
    setSelected((prev) => {
      const next = { ...prev };
      if (next[id]) delete next[id];
      else next[id] = true;
      return next;
    });
  }

  function openDispatchSelected(): void {
    const items = sortedPRs.filter((pr) => selected[prRowId(pr)]);
    setDispatchModal({ items, mode: "selected" });
    setModalPrompt("");
  }

  function openDispatchAll(): void {
    if (!window.confirm("Dispatch to all " + sortedPRs.length + " visible PRs?"))
      return;
    setDispatchModal({ items: sortedPRs, mode: "all" });
    setModalPrompt("");
  }

  function doDispatch(): void {
    if (!dispatchModal || !dispatchModal.items.length) return;
    setDispatching(true);
    const payload = {
      selection: {
        mode: "list",
        items: dispatchModal.items.map((pr) => ({
          repo: pr.repo || pr.repository || pr.full_name,
          number: pr.number || pr.pr_number,
          title: pr.title,
        })),
      },
      provider: modalProvider,
      prompt: modalPrompt,
      confirmation: { approved_by: principalName || "anonymous" },
    };
    legacyFetch("/api/prs/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((r) => {
        if (!r.ok)
          return r.json().then((e: { detail?: string }) => {
            throw new Error(e.detail || String(r.status));
          });
        return r.json();
      })
      .then(() => {
        setDispatchMsg({
          type: "success",
          text:
            "Dispatched " +
            dispatchModal.items.length +
            " PR(s) to " +
            modalProvider,
        });
        setDispatchModal(null);
        setSelected({});
        setTimeout(() => {
          setDispatchMsg(null);
        }, 6000);
      })
      .catch((err: Error) => {
        setDispatchMsg({ type: "error", text: "Dispatch failed: " + err.message });
        setTimeout(() => {
          setDispatchMsg(null);
        }, 8000);
      })
      .finally(() => {
        setDispatching(false);
      });
  }

  return (
    <div>
      {dispatchMsg ? (
        <div
          role="alert"
          style={{
            marginBottom: 12,
            padding: "10px 16px",
            borderRadius: 6,
            background:
              dispatchMsg.type === "error"
                ? "rgba(248,81,73,0.15)"
                : "rgba(63,185,80,0.15)",
            color:
              dispatchMsg.type === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)",
            border:
              "1px solid " +
              (dispatchMsg.type === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)"),
            fontSize: 13,
          }}
        >
          {dispatchMsg.text}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 12,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <input
          type="text"
          placeholder="Filter by repo (org/repo)…"
          value={repoFilter}
          onChange={(e) => setRepoFilter(e.target.value)}
          style={{
            flex: "1 1 160px",
            minWidth: 140,
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 12,
          }}
        />
        <input
          type="text"
          placeholder="Filter by author…"
          value={authorFilter}
          onChange={(e) => setAuthorFilter(e.target.value)}
          style={{
            flex: "1 1 120px",
            minWidth: 100,
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 12,
          }}
        />
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            fontSize: 12,
            color: "var(--text-secondary)",
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          <input
            type="checkbox"
            checked={showDrafts}
            onChange={(e) => setShowDrafts(e.target.checked)}
          />
          Show drafts
        </label>
        <button
          className="btn"
          onClick={fetchPRs}
          disabled={loading}
          style={{ marginLeft: "auto", whiteSpace: "nowrap" }}
        >
          {loading ? <span className="spinner" /> : <RefreshGlyph size={12} />} Refresh
        </button>
      </div>

      {loading && prs.length === 0 ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "24px",
            color: "var(--text-muted)",
            fontSize: 13,
          }}
        >
          <span className="spinner" />
          Loading PRs…
        </div>
      ) : fetchError ? (
        <div
          style={{
            padding: "12px 16px",
            borderRadius: 8,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
            fontSize: 12,
          }}
        >
          {fetchError}
        </div>
      ) : sortedPRs.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            padding: "32px 24px",
            color: "var(--text-muted)",
            fontSize: 13,
          }}
        >
          No open PRs found.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th style={{ width: 32, padding: "8px 10px" }}>
                  <input
                    type="checkbox"
                    checked={allVisible}
                    onChange={toggleAll}
                    title={allVisible ? "Deselect all" : "Select all"}
                  />
                </th>
                <SortTh label="Repo" sortKey="repo" sort={sort} setSort={setSort} />
                <SortTh
                  label="#"
                  sortKey="number"
                  sort={sort}
                  setSort={setSort}
                  thProps={{ style: { width: 60 } }}
                />
                <SortTh label="Title" sortKey="title" sort={sort} setSort={setSort} />
                <SortTh
                  label="Author"
                  sortKey="author"
                  sort={sort}
                  setSort={setSort}
                />
                <SortTh
                  label="Age"
                  sortKey="age"
                  sort={sort}
                  setSort={setSort}
                  thProps={{ style: { width: 60 } }}
                />
                <th>Draft</th>
                <th>Labels</th>
                <th>Claim</th>
              </tr>
            </thead>
            <tbody>
              {sortedPRs.map((pr) => {
                const id = prRowId(pr);
                const isChecked = !!selected[id];
                const repo = pr.repo || pr.repository || pr.full_name || "-";
                const prNum = pr.number || pr.pr_number || "-";
                const repoUrl =
                  pr.repo_url ||
                  pr.repository_url ||
                  "https://github.com/" + repo;
                const prUrl = pr.html_url || pr.url || repoUrl + "/pull/" + prNum;
                const author = pr.author || (pr.user && pr.user.login) || "-";
                const labels = pr.labels || [];
                const labelNames = labels.map((l) =>
                  typeof l === "string" ? l : l.name || "",
                );
                const shownLabels = labelNames.slice(0, 3);
                const extraLabels = labelNames.length - 3;
                const titleFull = pr.title || "-";
                const titleShort =
                  titleFull.length > 80
                    ? titleFull.slice(0, 77) + "…"
                    : titleFull;
                const claim = pr.agent_claim || "";

                return (
                  <tr
                    key={id}
                    style={{
                      background: isChecked
                        ? "rgba(88,166,255,0.06)"
                        : undefined,
                    }}
                  >
                    <td style={{ width: 32, padding: "8px 10px" }}>
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleRow(id)}
                      />
                    </td>
                    <td>
                      <a
                        href={repoUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          color: "var(--accent-blue)",
                          textDecoration: "none",
                          fontSize: 12,
                        }}
                      >
                        {repo}
                      </a>
                    </td>
                    <td>
                      <a
                        href={prUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          color: "var(--accent-blue)",
                          textDecoration: "none",
                          fontSize: 12,
                        }}
                      >
                        {"#" + prNum}
                      </a>
                    </td>
                    <td
                      title={titleFull}
                      style={{
                        maxWidth: 320,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {titleShort}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                      {author}
                    </td>
                    <td style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                      {prAgeLabel(pr)}
                    </td>
                    <td>
                      {pr.draft ? (
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 7px",
                            borderRadius: 10,
                            background: "rgba(139,148,158,0.18)",
                            color: "var(--text-muted)",
                            fontWeight: 500,
                          }}
                        >
                          Draft
                        </span>
                      ) : null}
                    </td>
                    <td style={{ fontSize: 11 }}>
                      {shownLabels.map((lbl) => (
                        <span
                          key={lbl}
                          style={{
                            display: "inline-block",
                            marginRight: 3,
                            padding: "2px 6px",
                            borderRadius: 10,
                            background: "rgba(88,166,255,0.12)",
                            color: "var(--accent-blue)",
                            fontSize: 11,
                            fontWeight: 500,
                            whiteSpace: "nowrap",
                          }}
                        >
                          {lbl}
                        </span>
                      ))}
                      {extraLabels > 0 ? (
                        <span
                          style={{
                            fontSize: 11,
                            color: "var(--text-muted)",
                            marginLeft: 2,
                          }}
                        >
                          {"+" + extraLabels}
                        </span>
                      ) : null}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      {claim}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedIds.length > 0 ? (
        <div
          style={{
            marginTop: 12,
            padding: "10px 14px",
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            display: "flex",
            gap: 10,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <span
            style={{
              fontSize: 12,
              color: "var(--text-secondary)",
              marginRight: 4,
            }}
          >
            {selectedIds.length + " PR(s) selected"}
          </span>
          <button className="btn" onClick={openDispatchSelected}>
            {"Dispatch to selected (" + selectedIds.length + ")"}
          </button>
          <button
            className="btn"
            style={{ opacity: 0.8 }}
            onClick={openDispatchAll}
          >
            {"Dispatch to all (" + sortedPRs.length + ")"}
          </button>
        </div>
      ) : sortedPRs.length > 0 ? (
        <div
          style={{
            marginTop: 10,
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <button
            className="btn"
            style={{ opacity: 0.7 }}
            onClick={openDispatchAll}
          >
            {"Dispatch to all (" + sortedPRs.length + ")"}
          </button>
        </div>
      ) : null}

      {dispatchModal ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setDispatchModal(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setDispatchModal(null);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="dispatch-modal-title"
            style={{
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              padding: 24,
              minWidth: 400,
              maxWidth: 560,
              maxHeight: "80vh",
              overflowY: "auto",
            }}
          >
            <div
              id="dispatch-modal-title"
              style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}
            >
              {"Dispatch to " + dispatchModal.items.length + " PR(s)"}
            </div>

            <div
              style={{
                maxHeight: 180,
                overflowY: "auto",
                marginBottom: 14,
                border: "1px solid var(--border)",
                borderRadius: 6,
                background: "var(--bg-secondary)",
                padding: "6px 10px",
              }}
            >
              {dispatchModal.items.map((pr) => {
                const repo = pr.repo || pr.repository || pr.full_name || "-";
                const num = pr.number || pr.pr_number || "-";
                return (
                  <div
                    key={String(num)}
                    style={{
                      fontSize: 12,
                      padding: "3px 0",
                      borderBottom: "1px solid var(--border)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {repo + " #" + num + " — " + (pr.title || "")}
                  </div>
                );
              })}
            </div>

            <label
              style={{
                display: "block",
                fontSize: 12,
                color: "var(--text-secondary)",
                marginBottom: 8,
              }}
            >
              Provider
              <select
                value={modalProvider}
                onChange={(e) => setModalProvider(e.target.value)}
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
              >
                {PROVIDERS.map((entry) => (
                  <option key={entry[0]} value={entry[0]}>
                    {entry[1]}
                  </option>
                ))}
              </select>
            </label>

            <label
              style={{
                display: "block",
                fontSize: 12,
                color: "var(--text-secondary)",
                marginBottom: 14,
              }}
            >
              Prompt (optional)
              <textarea
                value={modalPrompt}
                onChange={(e) => setModalPrompt(e.target.value)}
                rows={4}
                placeholder="Describe what the agent should do with each PR…"
                style={{
                  display: "block",
                  width: "100%",
                  marginTop: 4,
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "6px 10px",
                  fontSize: 12,
                  fontFamily: "inherit",
                  resize: "vertical",
                  boxSizing: "border-box",
                }}
              />
            </label>

            <div style={{ display: "flex", gap: 8 }}>
              <button
                className="btn"
                onClick={doDispatch}
                disabled={dispatching}
              >
                {dispatching ? <span className="spinner" /> : null}
                {dispatching ? " Dispatching…" : "Confirm dispatch"}
              </button>
              <button
                className="btn"
                style={{ opacity: 0.7 }}
                onClick={() => setDispatchModal(null)}
                disabled={dispatching}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default RemediationPRsSubTab;
