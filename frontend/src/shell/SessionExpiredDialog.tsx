import React from "react";
import { Dialog, DialogContent, DialogTitle, TouchButton } from "../primitives";
import { subscribeSessionExpired } from "../lib/sessionExpired";

export interface SessionExpiredDialogProps {
  open: boolean;
  onClose: () => void;
}

export function SessionExpiredDialog({ open, onClose }: SessionExpiredDialogProps) {
  const buttonRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (open) {
      buttonRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  const reauthenticate = () => {
    window.location.href = "/api/auth/github";
  };

  return (
    <Dialog
      ariaDescribedBy="session-expired-dialog-description"
      className="legacy-session-dialog"
      open={open}
      onClose={onClose}
      closeOnOverlayClick={false}
    >
      <DialogTitle className="legacy-session-dialog__title">Session Expired</DialogTitle>
      <DialogContent className="legacy-session-dialog__content">
        <p
          id="session-expired-dialog-description"
          className="legacy-session-dialog__copy"
        >
          Your session has expired. Re-authenticate to continue using the dashboard.
        </p>
        <TouchButton ref={buttonRef} onClick={reauthenticate} variant="primary">
          Re-authenticate
        </TouchButton>
      </DialogContent>
    </Dialog>
  );
}


/**
 * Opens the Session Expired dialog when the API layer or the fetch guard emits
 * a session-expired event (#1345). Mounted once by the shell; before #1345 only
 * the Classic layout mounted it, so an expired session in the modern shell
 * failed silently.
 */
export function SessionExpiredHost() {
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => subscribeSessionExpired(() => setOpen(true)), []);
  return <SessionExpiredDialog open={open} onClose={() => setOpen(false)} />;
}
