/**
 * ConsoleErrorBanner.tsx — visible, dismissible Staff Console failure (#1446).
 *
 * Shared by the desktop and mobile layouts so a failed roster load, thread
 * open, send or approval is never dropped silently.
 */
import type { ConsoleError, ConsoleErrorKind } from "./useStaffConsole";

const ACTION_BY_KIND: Record<ConsoleErrorKind, string> = {
  roster: "Could not load the staff roster",
  thread: "Could not open the conversation",
  send: "Message not sent",
  decision: "Decision not recorded",
};

export interface ConsoleErrorBannerProps {
  error: ConsoleError | null;
  onDismiss: () => void;
}

export function ConsoleErrorBanner({ error, onDismiss }: ConsoleErrorBannerProps) {
  if (!error) return null;
  return (
    <div className="staff-console__error" role="alert" data-testid={`staff-console-error-${error.kind}`}>
      <span>
        <strong>{ACTION_BY_KIND[error.kind]}:</strong> {error.message}
      </span>
      <button type="button" onClick={onDismiss} aria-label="Dismiss error">
        ✕
      </button>
    </div>
  );
}
