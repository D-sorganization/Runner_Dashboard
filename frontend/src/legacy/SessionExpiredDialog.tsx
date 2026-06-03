import React from "react";
import { Dialog, DialogContent, DialogTitle, TouchButton } from "../primitives";

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

