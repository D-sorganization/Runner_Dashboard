import React from "react";

export type SaveState = "idle" | "saving" | "saved" | "error";

export interface SaveIndicatorProps {
  state: SaveState;
  onRetry?: () => void;
}

/**
 * SaveIndicator — accessible save-state feedback primitive.
 *
 * - "saving"  → "Saving…" with a spinner
 * - "saved"   → "Saved ✓" with success colour
 * - "error"   → "Failed — Retry" with an actionable button
 * - "idle"    → renders nothing
 */
export const SaveIndicator: React.FC<SaveIndicatorProps> = ({
  state,
  onRetry,
}) => {
  if (state === "idle") return null;

  const baseStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    fontSize: 13,
    fontWeight: 500,
    lineHeight: 1.4,
    padding: "4px 8px",
    borderRadius: 6,
    transition: "color 120ms ease",
  };

  if (state === "saving") {
    return (
      <span
        aria-busy="true"
        aria-live="polite"
        className="save-indicator saving"
        data-save-state="saving"
        role="status"
        style={{
          ...baseStyle,
          color: "var(--text-secondary)",
        }}
      >
        <span
          className="save-spinner"
          style={{
            display: "inline-block",
            width: 12,
            height: 12,
            border: "2px solid var(--border-light)",
            borderTopColor: "var(--accent-blue)",
            borderRadius: "50%",
            animation: "spin 0.8s linear infinite",
          }}
        />
        Saving…
      </span>
    );
  }

  if (state === "saved") {
    return (
      <span
        aria-live="polite"
        className="save-indicator saved"
        data-save-state="saved"
        role="status"
        style={{
          ...baseStyle,
          color: "var(--accent-green)",
        }}
      >
        Saved ✓
      </span>
    );
  }

  // error
  return (
    <span
      aria-live="assertive"
      className="save-indicator error"
      data-save-state="error"
      role="alert"
      style={{
        ...baseStyle,
        color: "var(--accent-red)",
      }}
    >
      Failed
      {onRetry && (
        <>
          {" — "}
          <button
            aria-label="Retry save"
            className="save-retry-btn"
            onClick={onRetry}
            style={{
              background: "transparent",
              border: "none",
              color: "inherit",
              cursor: "pointer",
              fontSize: "inherit",
              fontWeight: 600,
              padding: 0,
              textDecoration: "underline",
            }}
            type="button"
          >
            Retry
          </button>
        </>
      )}
    </span>
  );
};
