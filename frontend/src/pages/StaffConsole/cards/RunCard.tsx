import React, { useState } from "react";
import type { RunAnswerHandler, RunCancelHandler, RunCardData, RunStatus } from "./cardTypes";
import "./cards.css";

export interface RunCardProps {
  run: RunCardData;
  onCancel?: RunCancelHandler;
  onAnswer?: RunAnswerHandler;
  className?: string;
}

/** The question of a needs-input run, and the answer form until it is answered (#1547). */
const RunQuestion: React.FC<{ run: RunCardData; onAnswer?: RunAnswerHandler }> = ({ run, onAnswer }) => {
  const [answer, setAnswer] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const answered = Boolean(run.continued_by);

  const submit = async () => {
    if (!onAnswer || !answer.trim()) return;
    setSending(true);
    const ok = await onAnswer(run.id, answer.trim());
    setSending(false);
    setSent(ok);
  };

  return (
    <div className="staff-run-card__question" style={{ margin: "8px 0", fontSize: 13 }}>
      <div style={{ color: "var(--text-primary)", marginBottom: 8, fontWeight: 500 }}>{run.question}</div>
      {answered ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Answered by {run.answered_by || "an operator"}; continued in run {run.continued_by}.
        </div>
      ) : sent ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Answer sent.</div>
      ) : onAnswer ? (
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          <textarea
            aria-label="Answer"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={2}
            disabled={sending}
            style={{
              flex: 1,
              fontSize: 12,
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm, 6px)",
              color: "var(--text-primary)",
              padding: "6px 10px",
              fontFamily: "inherit",
              resize: "vertical",
            }}
          />
          <button
            type="button"
            onClick={submit}
            disabled={sending || !answer.trim()}
            style={{
              background: "var(--accent-blue)",
              color: "var(--text-on-accent)",
              border: "none",
              borderRadius: "var(--radius-sm, 6px)",
              padding: "6px 12px",
              fontSize: 12,
              fontWeight: 600,
              cursor: sending || !answer.trim() ? "not-allowed" : "pointer",
            }}
          >
            Send answer
          </button>
        </div>
      ) : null}
    </div>
  );
};

function getStatusBadgeStyle(status: RunStatus): { bg: string; text: string; border: string } {
  switch (status) {
    case "running":
      return { bg: "var(--badge-info-bg)", text: "var(--accent-blue, #58a6ff)", border: "var(--accent-blue, #1f6feb)" };
    case "completed":
      return { bg: "var(--badge-success-bg)", text: "var(--accent-green, #3fb950)", border: "var(--accent-green, #2ea043)" };
    case "failed":
      return { bg: "var(--badge-danger-bg)", text: "var(--accent-red, #f85149)", border: "var(--accent-red, #da3633)" };
    case "cancelled":
      return { bg: "var(--badge-neutral-bg)", text: "var(--text-muted, #8b949e)", border: "var(--border, #6e7681)" };
    case "queued":
    default:
      return { bg: "var(--badge-warning-bg)", text: "var(--accent-yellow, #d29922)", border: "var(--accent-yellow, #bb8009)" };
  }
}

function formatDuration(seconds?: number): string {
  if (seconds === undefined || seconds === null) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const remSecs = Math.round(seconds % 60);
  return `${mins}m ${remSecs}s`;
}

export const RunCard: React.FC<RunCardProps> = ({
  run,
  onCancel,
  onAnswer,
  className = "",
}) => {
  const [showLogs, setShowLogs] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const statusStyle = getStatusBadgeStyle(run.status);
  const isActive = run.status === "running" || run.status === "queued";

  const cancel = async () => {
    if (!onCancel) return;
    setCancelling(true);
    const ok = await onCancel(run.id);
    if (ok === false) setCancelling(false);
  };

  return (
    <div
      className={`staff-run-card ${className}`}
      data-run-id={run.id}
    >
      {/* Header: Title and Status */}
      <div className="staff-card-header">
        <div className="staff-card-header-left">
          <span className="staff-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          </span>
          <span className="staff-card-title">
            Run {run.run_number ? `#${run.run_number}` : run.id}
          </span>
        </div>
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            fontWeight: 700,
            padding: "2px 6px",
            borderRadius: 4,
            background: statusStyle.bg,
            color: statusStyle.text,
            border: `1px solid ${statusStyle.border}`,
            flexShrink: 0,
          }}
        >
          {run.status}
        </span>
      </div>

      {/* Metadata Key/Value Grid */}
      {(run.node || run.provider || run.elapsed_seconds !== undefined) && (
        <div className="staff-card-grid">
          {run.node && (
            <div className="staff-card-kv">
              <span className="staff-card-k">Node</span>
              <span className="staff-card-v">{run.node}</span>
            </div>
          )}
          {run.provider && (
            <div className="staff-card-kv">
              <span className="staff-card-k">Model</span>
              <span className="staff-card-v">{run.provider}</span>
            </div>
          )}
          {run.elapsed_seconds !== undefined && (
            <div className="staff-card-kv">
              <span className="staff-card-k">Elapsed</span>
              <span className="staff-card-v">{formatDuration(run.elapsed_seconds)}</span>
            </div>
          )}
        </div>
      )}

      {run.status === "needs_input" && run.question && <RunQuestion run={run} onAnswer={onAnswer} />}
      {run.status === "completed" && run.summary && (
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8, lineHeight: 1.5 }}>
          {run.summary}
        </div>
      )}
      {run.status === "failed" && run.error && (
        <div role="alert" style={{ fontSize: 12, color: "var(--accent-red)", marginBottom: 8 }}>
          {run.error}
        </div>
      )}

      {/* Expandable Logs Tail */}
      {run.logs_tail && run.logs_tail.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <button
            type="button"
            onClick={() => setShowLogs((prev) => !prev)}
            style={{
              background: "none",
              border: "none",
              color: "var(--accent-blue, #58a6ff)",
              fontSize: 11,
              padding: 0,
              cursor: "pointer",
              textDecoration: "underline",
            }}
          >
            {showLogs ? "Hide logs" : "View logs"}
          </button>
          {showLogs && (
            <pre
              style={{
                fontSize: 11,
                fontFamily: "var(--font-mono, monospace)",
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                padding: "8px 10px",
                borderRadius: 4,
                marginTop: 6,
                maxHeight: 140,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                border: "1px solid var(--border)",
              }}
            >
              {run.logs_tail.join("\n")}
            </pre>
          )}
        </div>
      )}

      {/* Footer links and Cancel action */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
        <div style={{ display: "flex", gap: 8, fontSize: 12 }}>
          {run.run_url && (
            <a
              href={run.run_url}
              style={{ color: "var(--accent-blue, #58a6ff)", textDecoration: "none", fontWeight: 500 }}
            >
              View run
            </a>
          )}
          {run.pr_url && (
            <a
              href={run.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--accent-purple, #bc8cff)", textDecoration: "none", fontWeight: 500 }}
            >
              PR #{run.pr_number || "link"}
            </a>
          )}
        </div>

        {isActive && onCancel && (
          <button
            type="button"
            onClick={cancel}
            disabled={cancelling}
            className="staff-action-card__btn staff-action-card__btn--deny"
            style={{ padding: "3px 10px", fontSize: 11 }}
          >
            Cancel run
          </button>
        )}
      </div>
    </div>
  );
};
