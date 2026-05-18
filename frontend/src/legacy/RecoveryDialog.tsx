import React from "react";

export interface RecoveryDialogProps {
  onClose: () => void;
  /**
   * Optional override for the health-check URL surfaced in diagnostic messages.
   * Defaults to ``${window.location.origin}/health`` at render time, which is
   * what the legacy App.tsx polls. Exposed for tests.
   */
  healthUrl?: string;
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function isDesktopPlatform() {
  if (typeof navigator === "undefined") return false;
  return navigator.platform.includes("Win32") || navigator.platform.includes("Mac");
}

function defaultHealthUrl(): string {
  if (typeof window === "undefined") return "/health";
  try {
    return `${window.location.origin}/health`;
  } catch {
    return "/health";
  }
}

export function RecoveryDialog({ onClose, healthUrl }: RecoveryDialogProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const startButtonRef = React.useRef<HTMLButtonElement>(null);
  const refreshButtonRef = React.useRef<HTMLButtonElement>(null);
  const [protocolError, setProtocolError] = React.useState<string | null>(null);
  const canUseProtocolHandler = isDesktopPlatform();
  const resolvedHealthUrl = healthUrl ?? defaultHealthUrl();

  React.useEffect(() => {
    (startButtonRef.current || refreshButtonRef.current)?.focus();
  }, []);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }

    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;

    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector));

    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const handleStartNow = () => {
    // Most browsers only honour custom URL protocol handlers when the page is
    // served over HTTPS *and* the protocol has been registered on the host OS
    // (see deploy/register-protocol.ps1). We still attempt the handler in any
    // context so an operator who has it registered can use it, but we always
    // surface an actionable diagnostic explaining what to try if nothing
    // happens after the click — the previous "Make sure you're using HTTPS"
    // message was misleading (issue: dashboard backend HTTPS mixed content).
    try {
      window.location.href = "runner-dashboard://start";
    } catch {
      // window.location assignment can throw in sandboxed iframes — ignore.
    }

    const origin = (() => {
      try {
        return window.location.origin;
      } catch {
        return "(unknown origin)";
      }
    })();
    setProtocolError(
      `If nothing happened: the runner-dashboard:// handler is not registered on this device, ` +
        `or the page (${origin}) is not allowed to invoke it. ` +
        `Run "systemctl --user restart runner-dashboard" on the dashboard host, then click Refresh. ` +
        `Backend health URL: ${resolvedHealthUrl}`,
    );
  };

  return (
    <div
      aria-describedby="recovery-dialog-description"
      aria-labelledby="recovery-dialog-title"
      aria-modal="true"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onKeyDown={handleKeyDown}
      role="dialog"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        cursor: "pointer",
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        style={{
          background: "var(--bg-primary)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "24px",
          minWidth: 400,
          maxWidth: 560,
          maxHeight: "80vh",
          overflow: "auto",
          cursor: "default",
          boxShadow: "0 10px 40px rgba(0,0,0,0.3)",
        }}
        onClick={(event) => {
          event.stopPropagation();
        }}
      >
        <h2 id="recovery-dialog-title" style={{ margin: "0 0 16px 0", color: "var(--accent-red)" }}>
          Backend Not Responding
        </h2>
        <div aria-live="assertive" id="recovery-dialog-description">
          <p style={{ margin: "0 0 12px 0", color: "var(--text-secondary)", fontSize: "14px" }}>
            {canUseProtocolHandler
              ? 'The dashboard backend is not responding. Click "Start Now" to invoke the registered runner-dashboard:// handler, or run the terminal command below on the dashboard host.'
              : "The dashboard backend is not responding. To restart the service, run this command on the dashboard host:"}
          </p>
          <pre style={{ background: "var(--bg-secondary)", padding: "12px", borderRadius: 4, margin: "0 0 16px 0", fontSize: "13px", overflow: "auto", color: "var(--text-primary)" }}>
            {"systemctl --user restart runner-dashboard\n\nThen click Refresh."}
          </pre>
          <p style={{ margin: "0 0 16px 0", color: "var(--text-tertiary, var(--text-secondary))", fontSize: "12px" }}>
            Health probe: <code>{resolvedHealthUrl}</code>
          </p>
        </div>
        {protocolError ? (
          <p
            aria-live="polite"
            role="alert"
            style={{ margin: "0 0 12px 0", color: "var(--accent-red)", fontSize: "13px" }}
          >
            {protocolError}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: "12px", justifyContent: "flex-end" }}>
          {canUseProtocolHandler ? (
            <button
              ref={startButtonRef}
              onClick={handleStartNow}
              style={{
                padding: "8px 16px",
                background: "var(--accent-green)",
                color: "white",
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: "14px",
                fontWeight: "500",
              }}
            >
              Start Now
            </button>
          ) : null}
          <button
            ref={refreshButtonRef}
            onClick={onClose}
            style={{
              padding: "8px 16px",
              background: "var(--bg-secondary)",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              cursor: "pointer",
              fontSize: "14px",
            }}
          >
            Refresh
          </button>
        </div>
      </div>
    </div>
  );
}
