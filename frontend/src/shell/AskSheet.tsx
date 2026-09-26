/**
 * AskSheet.tsx — Mobile Ask sheet with composer (SC-G5-3, #1499).
 *
 * Replaces the legacy AgentDispatch sheet on mobile. The mobile FAB opens this Ask sheet,
 * allowing the operator to send a task or question directly to Barb (or navigate to the
 * full Staff Console).
 */
import React, { useCallback, useState } from "react";
import { TouchButton } from "../primitives/TouchButton";
import { errorMessage, submitStaffRequest, type StaffRequestResponse } from "../pages/Staff/staffApi";

export interface AskSheetProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenStaffConsole?: () => void;
}

export function AskSheet({ isOpen, onClose, onOpenStaffConsole }: AskSheetProps) {
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StaffRequestResponse | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const text = prompt.trim();
      if (!text || submitting) return;

      setSubmitting(true);
      setError(null);
      try {
        const resp = await submitStaffRequest({
          kind: "staff.dispatch",
          role: null,
          prompt: text,
          machine: "local",
          dry_run: false,
          target: { repo: "", ref: "" },
        });
        setResult(resp);
        setPrompt("");
      } catch (err: unknown) {
        setError(errorMessage(err));
      } finally {
        setSubmitting(false);
      }
    },
    [prompt, submitting],
  );

  const handleReset = useCallback(() => {
    setResult(null);
    setError(null);
    setPrompt("");
  }, []);

  if (!isOpen) return null;

  return (
    <div
      className="mobile-shell__sheet-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="mobile-shell__sheet"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Ask"
      >
        <div className="mobile-shell__sheet-header">
          <h2 className="mobile-shell__sheet-title">Ask</h2>
          <button
            className="mobile-shell__sheet-close"
            onClick={onClose}
            type="button"
            aria-label="Close ask sheet"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              width="20"
              height="20"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="mobile-shell__sheet-body" style={{ padding: "16px" }}>
          {result ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div
                style={{
                  background: "rgba(63, 185, 80, 0.12)",
                  border: "1px solid var(--accent-green)",
                  borderRadius: "8px",
                  padding: "12px",
                  color: "var(--accent-green)",
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: "4px" }}>Request Submitted</div>
                <div style={{ fontSize: "13px" }}>
                  {result.run_id ? `Run #${result.run_id}` : `State: ${result.state}`}
                  {result.work_item_id ? ` (Work item: ${result.work_item_id})` : ""}
                </div>
              </div>
              <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
                <TouchButton variant="primary" onClick={handleReset}>
                  Ask Another
                </TouchButton>
                {onOpenStaffConsole ? (
                  <TouchButton
                    variant="default"
                    onClick={() => {
                      onClose();
                      onOpenStaffConsole();
                    }}
                  >
                    Open Staff Console →
                  </TouchButton>
                ) : null}
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <p style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary)" }}>
                Ask Barb or staff roles to run tasks, remediate issues, or investigate runners.
              </p>
              <textarea
                aria-label="Ask prompt"
                className="form-input"
                rows={4}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Ask staff a question or give an instruction…"
                disabled={submitting}
                style={{
                  width: "100%",
                  resize: "vertical",
                  padding: "10px",
                  borderRadius: "8px",
                  fontSize: "14px",
                }}
              />
              {error ? (
                <div
                  role="alert"
                  className="staff-error"
                  style={{
                    color: "var(--accent-red)",
                    fontSize: "13px",
                    padding: "8px",
                    background: "rgba(248, 81, 73, 0.1)",
                    borderRadius: "6px",
                  }}
                >
                  {error}
                </div>
              ) : null}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "4px" }}>
                {onOpenStaffConsole ? (
                  <button
                    type="button"
                    onClick={() => {
                      onClose();
                      onOpenStaffConsole();
                    }}
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--accent-blue)",
                      fontSize: "13px",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  >
                    Open full Console →
                  </button>
                ) : <span />}
                <TouchButton
                  type="submit"
                  variant="primary"
                  disabled={!prompt.trim() || submitting}
                >
                  {submitting ? "Sending…" : "Ask"}
                </TouchButton>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
