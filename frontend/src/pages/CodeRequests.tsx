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
  buildCodeRequest,
  type CodeRequestsProps,
  type DispatchStatus,
  type PromptTemplate,
  type SaveStatus,
  repoName,
} from "./codeRequestsTypes";
import { CodeRequestsHistory } from "./CodeRequestsHistory";
import { useProviderRegistry } from "../lib/useProviderRegistry";
import { PromptNotesEditor } from "./CodeRequestsPromptNotes";
import { errorMessage, submitStaffRequest } from "./Staff/staffApi";

export type * from "./codeRequestsTypes";

interface ProfileItem {
  id: string;
  name: string;
  provider: string;
  standards?: string[];
}

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
  const { registry } = useProviderRegistry();
  const targetUnavailable = dispatchTarget?.available === false;
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [selProfileId, setSelProfileId] = useState("");
  const [selRepo, setSelRepo] = useState("");
  const [selBranch, setSelBranch] = useState("main");
  const [selProvider, setSelProvider] = useState("");
  const [promptText, setPromptText] = useState("");
  const [selStds, setSelStds] = useState<Record<string, boolean>>({});
  const [templateName, setTemplateName] = useState("");
  const [dispatchStatus, setDispatchStatus] = useState<DispatchStatus>(null);
  const [dispatchError, setDispatchError] = useState("");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);

  useEffect(() => {
    fetch("/api/agent-profiles")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && Array.isArray(data.profiles)) {
          setProfiles(data.profiles);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selProvider && registry && registry.providers.length > 0) {
      const preferred = registry.providers.find((p) => p.loginStatus === "authenticated") || registry.providers[0];
      if (preferred) {
        setSelProvider(preferred.dashboardId);
      }
    }
  }, [registry, selProvider]);

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
    const activeProvider = selProvider || (registry?.providers[0]?.dashboardId || "codex");
    const dispatchFn = onDispatch ?? ((p) => submitStaffRequest(buildCodeRequest(p)));
    dispatchFn({
      repository: selRepo,
      branch: selBranch,
      provider: activeProvider,
      // Prompt notes and standards are applied server-side (#1501).
      prompt: promptText,
      standards: Object.keys(selStds).filter((k) => selStds[k]),
      profile_id: selProfileId || undefined,
    })
      .then(() => {
        setDispatchStatus("ok");
        onRefresh();
      })
      .catch((err: unknown) => {
        setDispatchError(errorMessage(err));
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
      <PromptNotesEditor promptNotes={promptNotes} onSavePromptNotes={onSavePromptNotes} />
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
                Profile
              </label>
              <select
                aria-label="Profile"
                value={selProfileId}
                onChange={(e) => {
                  const profId = e.target.value;
                  setSelProfileId(profId);
                  const found = profiles.find((p) => p.id === profId);
                  if (found) {
                    if (found.provider) setSelProvider(found.provider);
                    if (found.standards && Array.isArray(found.standards)) {
                      const newStds: Record<string, boolean> = {};
                      for (const s of found.standards) {
                        newStds[s] = true;
                      }
                      setSelStds(newStds);
                    }
                  }
                }}
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                  borderRadius: 4,
                  padding: "4px 8px",
                  minWidth: 120,
                }}
              >
                <option value="">— custom —</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}
              >
                Provider
              </label>
              <select
                aria-label="Provider"
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
                  minWidth: 140,
                }}
              >
                {registry && registry.providers.length > 0 ? (
                  registry.providers.map((p) => (
                    <option key={p.dashboardId} value={p.dashboardId}>
                      {p.label}
                      {p.loginStatus === "authenticated" ? " ✓" : ""}
                      {p.loginStatus === "unauthenticated" ? " ⚠️ (login req.)" : ""}
                    </option>
                  ))
                ) : (
                  <>
                    <option value="codex">Codex</option>
                    <option value="claude">Claude</option>
                    <option value="jules_api">Jules</option>
                  </>
                )}
              </select>
            </div>
          </div>
          {(() => {
            if (!registry) return null;
            const curP =
              registry.byDashboardId(selProvider) ||
              registry.providers.find((p) => p.id === selProvider || p.dashboardId === selProvider);
            if (curP && (curP.loginStatus === "unauthenticated" || curP.loginStatus === "error")) {
              return (
                <div
                  role="alert"
                  style={{
                    border: "1px solid var(--accent-amber, #d97706)",
                    borderRadius: 4,
                    padding: "6px 10px",
                    marginBottom: 10,
                    fontSize: 12,
                    color: "var(--accent-amber, #d97706)",
                    background: "rgba(217, 119, 6, 0.08)",
                  }}
                >
                  ⚠️ Warning: Provider &apos;{curP.label || selProvider}&apos; is currently unauthenticated or unavailable ({curP.loginDetail || "Credentials not configured"}). Dispatch may fail.
                </div>
              );
            }
            return null;
          })()}
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
