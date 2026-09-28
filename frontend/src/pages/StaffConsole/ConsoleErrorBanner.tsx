/**
 * ConsoleErrorBanner.tsx — visible, dismissible Staff Console failure (#1446).
 *
 * Shared by the desktop and mobile layouts so a failed roster load, thread
 * open, send or approval is never dropped silently.
 * Compact banner with details disclosure for long error blobs.
 */
import type { ConsoleError, ConsoleErrorKind } from "./useStaffConsole";

const ACTION_BY_KIND: Record<ConsoleErrorKind, string> = {
  roster: "Could not load the staff roster",
  thread: "Could not open the conversation",
  send: "Message not sent",
  decision: "Decision not recorded",
  run: "Run action refused",
};

export interface ConsoleErrorBannerProps {
  error: ConsoleError | null;
  onDismiss: () => void;
}

export function ConsoleErrorBanner({ error, onDismiss }: ConsoleErrorBannerProps) {
  if (!error) return null;
  const isLong = error.message.length > 140 || error.message.includes("\n") || error.message.startsWith("{");

  return (
    <div className="staff-console__error" role="alert" data-testid={`staff-console-error-${error.kind}`}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <span>
          <strong>{ACTION_BY_KIND[error.kind]}:</strong> {error.message}
        </span>
        {isLong && (
          <details style={{ marginTop: 4, fontSize: 11 }}>
            <summary style={{ cursor: "pointer", color: "inherit", opacity: 0.85 }}>Details</summary>
            <pre style={{ margin: "4px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 120, overflowY: "auto", fontFamily: "var(--font-mono, monospace)" }}>
              {error.message}
            </pre>
          </details>
        )}
      </div>
      <button type="button" onClick={onDismiss} aria-label="Dismiss error" style={{ background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "2px 6px" }}>
        ✕
      </button>
    </div>
  );
}
