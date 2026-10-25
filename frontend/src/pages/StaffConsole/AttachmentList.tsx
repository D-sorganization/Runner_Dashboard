import React from "react";
import type { ComposerAttachment, IntakeSensitivity } from "./threadTypes";

function sensitivityLabel(sensitivity: IntakeSensitivity): string {
  switch (sensitivity) {
    case "public_reference": return "Library reference";
    case "confidential_work": return "Confidential work";
    case "vault_sensitive": return "Vault sensitive";
  }
}

function statusLabel(status: ComposerAttachment["status"]): string {
  switch (status) {
    case "pending": return "Uploading…";
    case "attached": return "Ready";
    case "rejected": return "Needs attention";
  }
}

interface AttachmentListProps {
  attachments: ComposerAttachment[];
  onRemove: (id: string) => void;
}

export function AttachmentList({ attachments, onRemove }: AttachmentListProps) {
  if (attachments.length === 0) return null;
  return (
    <div className="staff-composer__attachments" aria-label="Staged attachments">
      {attachments.map((attachment) => (
        <span key={attachment.id} className={`staff-composer__chip staff-composer__chip--${attachment.status}`} data-testid={`composer-attachment-${attachment.filename}`}>
          <span className="staff-composer__chip-filename">{attachment.filename}</span>
          <button type="button" className="staff-composer__chip-remove" aria-label={`Remove ${attachment.filename}`} onClick={() => onRemove(attachment.id)}>Remove</button>
          <span className="staff-composer__chip-meta"><span className="staff-composer__chip-badge">{sensitivityLabel(attachment.sensitivity)}</span></span>
          <span className={`staff-composer__chip-status${attachment.error ? " staff-composer__chip-status--error" : ""}`} role="status" aria-live="polite">
            {statusLabel(attachment.status)}{attachment.error ? `: ${attachment.error}` : ""}
          </span>
        </span>
      ))}
    </div>
  );
}
