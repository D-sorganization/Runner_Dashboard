/**
 * CodeRequests.tsx — the "Code Requests" tab (CR-1, #1281).
 *
 * Dispatches a code request to plan and execute engineering work across fleet
 * repositories, optionally injecting engineering standards (TDD, DbC, DRY, LoD,
 * security, docs) and global prompt notes.
 */
import React, { useEffect, useState } from "react";
import { IssueGlyph } from "./decompIcons";
import {
  ALL_STANDARDS,
  type CodeRequestsProps,
  type DispatchStatus,
  type PromptTemplate,
  type SaveStatus,
  repoName,
} from "./codeRequestsTypes";
import { CodeRequestsHistory } from "./CodeRequestsHistory";

export type * from "./codeRequestsTypes";

export function CodeRequestsTab({
  repos = [],
  requests = [],
  dispatchTarget,
  templates = [],
  loading,
  promptNotes = { notes: "", enabled: true },
  onDispatch,
  onSaveTemplate,
  onSavePromptNotes,
  onRefresh,
}: CodeRequestsProps): React.ReactElement {
  const targetUnavailable = dispatchTarget?.available === false;
  const [selRepo, setSelRepo] = useState("");
  const [selBranch, setSelBranch] = useState("main");
  const [selProvider, setSelProvider] = useState("jules_api");
  const [promptText, setPromptText] = useState("");
  const [selStds, setSelStds] = useState<Record<string, boolean>>({});
  const [templateName, setTemplateName] = useState("");
  const [dispatchStatus, setDispatchStatus] = useState<DispatchStatus>(null);
  const [dispatchError, setDispatchError] = useState("");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);
  const [editingPromptNotes, setEditingPromptNotes] = useState(promptNotes.notes);
  const [promptNotesEnabled, setPromptNotesEnabled] = useState(promptNotes.enabled);
  const [promptNotesSaveStatus, setPromptNotesSaveStatus] = useState<SaveStatus>(null);

  useEffect(() => {
    setEditingPromptNotes(promptNotes.notes);
    setPromptNotesEnabled(promptNotes.enabled);
  }, [promptNotes.enabled, promptNotes.notes]);

  function toggleStd(s: string): void {
    setSelStds((prev) => {
      const next = Object.assign({}, prev);
      if (next[s]) {
        delete next[s];
      } else {
        next[s] = true;
      }
      return next;
    });
  }

  function doDispatch(): void {
    if (!selRepo || !promptText.trim()) return;
    setDispatchStatus("dispatching");
    let finalPrompt = promptText;
    if (promptNotesEnabled && editingPromptNotes.trim()) {
      finalPrompt = editingPromptNotes + "\n\n" + promptText;
    }
    onDispatch({
      repository: selRepo,
      branch: selBranch,
      provider: selProvider,
      prompt: finalPrompt,
      standards: Object.keys(selStds).filter((k) => selStds[k]),
    })
      .then(() => {
        setDispatchStatus("ok");
        onRefresh();
      })
      .catch((err: unknown) => {
        setDispatchError(err instanceof Error ? err.message : "");
        setDispatchStatus("error");
        onRefresh();
      });
  }

  function doSaveTemplate(): void {
    if (!templateName.trim() || !promptText.trim()) return;
    setSaveStatus("saving");
    onSaveTemplate({ name: templateName, prompt: promptText })
      .then(() => {
        setSaveStatus("ok");
      })
      .catch(() => {
        setSaveStatus("error");
      });
  }

  function loadTemplate(t: PromptTemplate): void {
    setPromptText(t.prompt);
  }

  function doSavePromptNotes(): void {
    setPromptNotesSaveStatus("saving");
    onSavePromptNotes({ notes: editingPromptNotes, enabled: promptNotesEnabled })
      .then(() => {
        setPromptNotesSaveStatus("ok");
        setTimeout(() => {
          setPromptNotesSaveStatus(null);
        }, 2000);
      })
      .catch(() => {
        setPromptNotesSaveStatus("error");
      });
  }

  return (
    <div style={{ padding: 20 }}>
      <div className="section-header">
        <IssueGlyph size={14} />
        Code Requests
      </div>
      {targetUnavailable ? (
        <div
          role="status"
          style={{
            border: "1px solid var(--accent-red)",
            borderRadius: 6,
            padding: 10,
            marginBottom: 12,
            fontSize: 13,
            color: "var(--text-primary)",
          }}
        >
          Dispatch is disabled: {dispatchTarget?.detail || "the dispatch workflow is unavailable"}.
          Code request dispatch is being replaced by Code Requests (#1279).
        </div>
      ) : null}
      <div
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 12,
          marginBottom: 20,
        }}
      >
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <input
              type="checkbox"
              checked={promptNotesEnabled}
              onChange={(e) => {
                setPromptNotesEnabled(e.target.checked);
              }}
              style={{ cursor: "pointer" }}
            />
            <label style={{ fontWeight: 600, fontSize: 13, cursor: "pointer", userSelect: "none" }}>
              Auto-inject Prompt Notes
            </label>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
            These notes will be automatically prepended to every prompt dispatch
          </div>
        </div>
        <textarea
          value={editingPromptNotes}
          onChange={(e) => {
            setEditingPromptNotes(e.target.value);
          }}
          placeholder="Enter global prompt notes that will be auto-added to every dispatch…"
          rows={6}
          style={{
            width: "100%",
            background: "var(--bg-primary)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
            borderRadius: 4,
            padding: 8,
            fontSize: 12,
            resize: "vertical",
            boxSizing: "border-box",
            fontFamily: "monospace",
          }}
        />
        <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
          <button
            className="action-btn secondary"
            onClick={doSavePromptNotes}
            style={{ padding: "4px 12px", fontSize: 12 }}
          >
            Save Notes
          </button>
          {promptNotesSaveStatus === "ok" ? (
            <div style={{ color: "var(--accent-green)", fontSize: 11 }}>✓ Saved</div>
          ) : null}
          {promptNotesSaveStatus === "error" ? (
            <div style={{ color: "var(--accent-red)", fontSize: 11 }}>✗ Failed</div>
          ) : null}
        </div>
      </div>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 500px" }}>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <div>
              <label
                style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}
              >
                Repository
              </label>
              <select
                value={selRepo}
                onChange={(e) => {
                  setSelRepo(e.target.value);
                }}
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  borderRadius: 4,
                  padding: "4px 8px",
                  minWidth: 160,
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
                style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}
              >
                Branch
              </label>
              <input
                value={selBranch}
                onChange={(e) => {
                  setSelBranch(e.target.value);
                }}
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  borderRadius: 4,
                  padding: "4px 8px",
                  width: 100,
                }}
              />
            </div>
            <div>
              <label
                style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}
              >
                Provider
              </label>
              <select
                value={selProvider}
                onChange={(e) => {
                  setSelProvider(e.target.value);
                }}
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
          </div>
          <div style={{ marginBottom: 10 }}>
            <label
              style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}
            >
              Standards to inject
            </label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {ALL_STANDARDS.map((s) => {
                const active = !!selStds[s];
                return (
                  <button
                    key={s}
                    onClick={() => {
                      toggleStd(s);
                    }}
                    style={{
                      padding: "3px 10px",
                      borderRadius: 12,
                      fontSize: 11,
                      cursor: "pointer",
                      border: "1px solid " + (active ? "var(--accent-purple)" : "var(--border)"),
                      background: active ? "rgba(160,130,220,0.15)" : "var(--bg-secondary)",
                      color: active ? "var(--accent-purple)" : "var(--text-muted)",
                    }}
                  >
                    {s.toUpperCase()}
                  </button>
                );
              })}
            </div>
          </div>
          <textarea
            value={promptText}
            onChange={(e) => {
              setPromptText(e.target.value);
            }}
            placeholder="Describe the code request to plan and execute…"
            rows={8}
            style={{
              width: "100%",
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              borderRadius: 4,
              padding: 8,
              fontSize: 13,
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <button
              className="action-btn"
              disabled={targetUnavailable || !selRepo || !promptText.trim()}
              onClick={doDispatch}
            >
              <IssueGlyph size={14} /> Dispatch
            </button>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                value={templateName}
                onChange={(e) => {
                  setTemplateName(e.target.value);
                }}
                placeholder="Template name…"
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  borderRadius: 4,
                  padding: "4px 8px",
                  fontSize: 12,
                  width: 160,
                }}
              />
              <button
                className="action-btn secondary"
                disabled={!templateName.trim() || !promptText.trim()}
                onClick={doSaveTemplate}
              >
                Save Template
              </button>
            </div>
          </div>
          {dispatchStatus === "ok" ? (
            <div style={{ color: "var(--accent-green)", fontSize: 13, marginTop: 8 }}>
              Code request dispatched.
            </div>
          ) : null}
          {dispatchStatus === "error" ? (
            <div role="alert" style={{ color: "var(--accent-red)", fontSize: 13, marginTop: 8 }}>
              {dispatchError ? "Dispatch failed: " + dispatchError : "Dispatch failed."}
            </div>
          ) : null}
          {saveStatus === "ok" ? (
            <div style={{ color: "var(--accent-green)", fontSize: 13, marginTop: 4 }}>
              Template saved.
            </div>
          ) : null}
          <CodeRequestsHistory requests={requests} loading={loading} />
        </div>
        <div style={{ flex: "0 1 240px" }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Saved Templates</div>
          {templates.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>No saved templates.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {templates.map((t, i) => (
                <div
                  key={i}
                  style={{
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: "8px 10px",
                    cursor: "pointer",
                  }}
                  onClick={() => {
                    loadTemplate(t);
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 12, color: "var(--text-primary)" }}>
                    {t.name}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    {(t.prompt || "").slice(0, 60) + "…"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function FeatureRequestsTab(props: CodeRequestsProps): React.ReactElement {
  return <CodeRequestsTab {...props} />;
}

export default CodeRequestsTab;
