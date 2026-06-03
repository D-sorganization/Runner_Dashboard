import React from "react";
import { Dialog, DialogActions, DialogContent, DialogTitle, TouchButton } from "../primitives";

export interface RecoveryDialogProps {
  onClose: () => void;
  /**
   * Optional override for the health-check URL surfaced in diagnostic messages.
   * Defaults to ``${window.location.origin}/health`` at render time, which is
   * what the legacy App.tsx polls. Exposed for tests.
   */
  healthUrl?: string;
}

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
  const [protocolError, setProtocolError] = React.useState<string | null>(null);
  const canUseProtocolHandler = isDesktopPlatform();
  const resolvedHealthUrl = healthUrl ?? defaultHealthUrl();

  React.useEffect(() => {
    startButtonRef.current?.focus();
  }, []);

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
        `Run "sudo systemctl restart runner-dashboard.service" on the dashboard host, then click Refresh. ` +
        `Backend health URL: ${resolvedHealthUrl}`,
    );
  };

  return (
    <Dialog
      ariaDescribedBy="recovery-dialog-description"
      className="legacy-recovery-dialog"
      open
      onClose={onClose}
    >
      <div ref={dialogRef}>
        <DialogTitle className="legacy-recovery-dialog__title">
          Backend Not Responding
        </DialogTitle>
        <DialogContent className="legacy-recovery-dialog__content">
          <div aria-live="assertive" id="recovery-dialog-description">
            <p className="legacy-recovery-dialog__copy">
              {canUseProtocolHandler
                ? 'The dashboard backend is not responding. Click "Start Now" to invoke the registered runner-dashboard:// handler, or run the terminal command below on the dashboard host.'
                : "The dashboard backend is not responding. To restart the service, run this command on the dashboard host:"}
            </p>
            <pre className="legacy-recovery-dialog__command">
              {"sudo systemctl restart runner-dashboard.service\n\nThen click Refresh."}
            </pre>
            <p className="legacy-recovery-dialog__probe">
              Health probe: <code>{resolvedHealthUrl}</code>
            </p>
          </div>
          {protocolError ? (
            <p
              aria-live="polite"
              className="legacy-recovery-dialog__error"
              role="alert"
            >
              {protocolError}
            </p>
          ) : null}
        </DialogContent>
        <DialogActions className="legacy-recovery-dialog__actions">
          {canUseProtocolHandler ? (
            <TouchButton
              ref={startButtonRef}
              onClick={handleStartNow}
              variant="primary"
            >
              Start Now
            </TouchButton>
          ) : null}
          <TouchButton onClick={onClose}>Refresh</TouchButton>
        </DialogActions>
      </div>
    </Dialog>
  );
}
