import React, { useState } from "react";
import type { RunAnswerHandler, RunCancelHandler, RunCardData, RunStatus } from "./cardTypes";

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
    <div className="staff-run-card__question" style={{ margin: "6px 0 8px", fontSize: 13 }}>
      <div style={{ color: "var(--text-primary, #c9d1d9)", marginBottom: 6 }}>{run.question}</div>
      {answered ? (
        <div style={{ fontSize: 12, color: "var(--text-muted, #8b949e)" }}>
          Answered by {run.answered_by || "an operator"}; continued in run {run.continued_by}.
        </div>
      ) : sent ? (
        <div style={{ fontSize: 12, color: "var(--text-muted, #8b949e)" }}>Answer sent.</div>
      ) : onAnswer ? (
        <div style={{ display: "flex", gap: 6 }}>
          <textarea
            aria-label="Answer"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={2}
            disabled={sending}
            style={{ flex: 1, fontSize: 12 }}
          />
          <button type="button" onClick={submit} disabled={sending || !answer.trim()}>
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
      return { bg: "rgba(31, 111, 235, 0.15)", text: "var(--accent-blue, #58a6ff)", border: "var(--border-blue, #1f6feb)" };
    case "completed":
      return { bg: "rgba(46, 160, 67, 0.15)", text: "var(--accent-green, #3fb950)", border: "var(--border-green, #2ea043)" };
    case "failed":
      return { bg: "rgba(248, 81, 73, 0.15)", text: "var(--accent-red, #f85149)", border: "var(--border-red, #da3633)" };
    case "cancelled":
      return { bg: "rgba(110, 118, 129, 0.15)", text: "var(--text-muted, #8b949e)", border: "var(--border-muted, #6e7681)" };
    case "queued":
    default:
      return { bg: "rgba(210, 153, 34, 0.15)", text: "var(--accent-yellow, #d29922)", border: "var(--border-yellow, #bb8009)" };
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
    // A refused cancel re-enables the button; an accepted one waits for the card update.
    if (ok === false) setCancelling(false);
  };

  return (
    <div
      className={`staff-run-card ${className}`}
      data-run-id={run.id}
      style={{
        border: "1px solid var(--border, #30363d)",
        borderRadius: 8,
        padding: "12px 16px",
        background: "var(--bg-secondary, #161b22)",
        maxWidth: 500,
        margin: "6px 0",
      }}
    >
      {/* Header: Title and Status */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary, #c9d1d9)" }}>
          Run {run.run_number ? `#${run.run_number}` : run.id}
        </span>
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
          }}
        >
          {run.status}
        </span>
      </div>

      {/* Metadata: Node, Provider, Elapsed */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, fontSize: 12, color: "var(--text-muted, #8b949e)", marginBottom: 8 }}>
        {run.node && (
          <div>
            Node: <strong style={{ color: "var(--text-secondary, #c9d1d9)" }}>{run.node}</strong>
          </div>
        )}
        {run.provider && (
          <div>
            Model: <strong style={{ color: "var(--text-secondary, #c9d1d9)" }}>{run.provider}</strong>
          </div>
        )}
        {run.elapsed_seconds !== undefined && (
          <div>
            Elapsed: <strong style={{ color: "var(--text-secondary, #c9d1d9)" }}>{formatDuration(run.elapsed_seconds)}</strong>
          </div>
        )}
      </div>

      {run.status === "needs_input" && run.question && <RunQuestion run={run} onAnswer={onAnswer} />}
      {run.status === "completed" && run.summary && (
        <div style={{ fontSize: 12, color: "var(--text-secondary, #c9d1d9)", marginBottom: 8 }}>{run.summary}</div>
      )}
      {run.status === "failed" && run.error && (
        <div role="alert" style={{ fontSize: 12, color: "var(--accent-red, #f85149)", marginBottom: 8 }}>
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
                fontFamily: "monospace",
                background: "var(--bg-primary, #0d1117)",
                color: "var(--text-primary, #c9d1d9)",
                padding: "8px 10px",
                borderRadius: 4,
                marginTop: 6,
                maxHeight: 140,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                border: "1px solid var(--border, #30363d)",
              }}
            >
              {run.logs_tail.join("\n")}
            </pre>
          )}
        </div>
      )}

      {/* Footer links and Cancel action */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 4 }}>
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
            style={{
              background: "transparent",
              color: "var(--accent-red, #f85149)",
              border: "1px solid var(--border-red, #da3633)",
              borderRadius: 6,
              padding: "2px 8px",
              fontSize: 11,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Cancel run
          </button>
        )}
      </div>
    </div>
  );
};
