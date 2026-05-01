import React from "react";

export interface SessionExpiredDialogProps {
  open: boolean;
  onClose: () => void;
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function SessionExpiredDialog({ open, onClose }: SessionExpiredDialogProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const buttonRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (open) {
      buttonRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

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

  const reauthenticate = () => {
    window.location.href = "/api/auth/github";
  };

  return (
    <div
      aria-describedby="session-expired-dialog-description"
      aria-labelledby="session-expired-dialog-title"
      aria-modal="true"
      onKeyDown={handleKeyDown}
      role="dialog"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15,17,23,0.8)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 24,
          maxWidth: 420,
          width: "100%",
          textAlign: "center",
          boxShadow: "0 10px 40px rgba(0,0,0,0.3)",
        }}
      >
        <h2
          id="session-expired-dialog-title"
          style={{ margin: "0 0 16px 0", color: "var(--accent-red)", fontSize: 20 }}
        >
          Session Expired
        </h2>
        <p
          id="session-expired-dialog-description"
          style={{ margin: "0 0 24px 0", color: "var(--text-secondary)", fontSize: 14, lineHeight: 1.45 }}
        >
          Your session has expired. Re-authenticate to continue using the dashboard.
        </p>
        <button
          ref={buttonRef}
          className="btn btn-blue"
          onClick={reauthenticate}
          style={{ textDecoration: "none" }}
          type="button"
        >
          Re-authenticate
        </button>
      </div>
    </div>
  );
}

