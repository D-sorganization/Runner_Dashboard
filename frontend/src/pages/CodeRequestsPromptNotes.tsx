/**
 * CodeRequestsPromptNotes.tsx — Global Prompt Notes editor subcomponent (CR-1 / CR-3).
 */
import React, { useEffect, useState } from "react";
import type { PromptNotes, SaveStatus } from "./codeRequestsTypes";

interface PromptNotesEditorProps {
  promptNotes: PromptNotes;
  onSavePromptNotes: (notes: PromptNotes) => Promise<unknown>;
}

export function PromptNotesEditor({
  promptNotes,
  onSavePromptNotes,
}: PromptNotesEditorProps): React.ReactElement {
  const [editingPromptNotes, setEditingPromptNotes] = useState(promptNotes.notes);
  const [promptNotesEnabled, setPromptNotesEnabled] = useState(promptNotes.enabled);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);

  useEffect(() => {
    setEditingPromptNotes(promptNotes.notes);
    setPromptNotesEnabled(promptNotes.enabled);
  }, [promptNotes.enabled, promptNotes.notes]);

  function doSave(): void {
    setSaveStatus("saving");
    onSavePromptNotes({ notes: editingPromptNotes, enabled: promptNotesEnabled })
      .then(() => {
        setSaveStatus("ok");
        setTimeout(() => setSaveStatus(null), 2000);
      })
      .catch(() => {
        setSaveStatus("error");
      });
  }

  return (
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
            onChange={(e) => setPromptNotesEnabled(e.target.checked)}
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
        onChange={(e) => setEditingPromptNotes(e.target.value)}
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
          onClick={doSave}
          style={{ padding: "4px 12px", fontSize: 12 }}
        >
          Save Notes
        </button>
        {saveStatus === "ok" ? (
          <div style={{ color: "var(--accent-green)", fontSize: 11 }}>✓ Saved</div>
        ) : null}
        {saveStatus === "error" ? (
          <div style={{ color: "var(--accent-red)", fontSize: 11 }}>✗ Failed</div>
        ) : null}
      </div>
    </div>
  );
}
