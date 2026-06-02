/**
 * IntroHeader — optional one-line orientation banner atop a page body
 * (issue #822). Seeded from the nav registry via `shell/intro.ts` (DRY).
 *
 * Accessibility: rendered as a complementary region with an accessible name so
 * screen-reader users can identify (and skip) it; the dismiss control has an
 * accessible label. Dismissal is purely local view state — the parent owns it.
 *
 * LoD: flat typed props; no reach into the registry (the caller resolves copy).
 */
import React from "react";

export interface IntroHeaderProps {
  /** Short tab title (used for the region's accessible name). */
  title: string;
  /** One-line orientation body. */
  body: string;
  /** Optional dismiss handler — renders a dismiss button when provided. */
  onDismiss?: () => void;
  /** Optional test id passthrough. */
  "data-testid"?: string;
}

export function IntroHeader({
  title,
  body,
  onDismiss,
  "data-testid": testId,
}: IntroHeaderProps): React.ReactElement {
  return (
    <aside
      aria-label={`About ${title}`}
      data-testid={testId}
      className="intro-header"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        margin: "12px 16px 0",
        padding: "10px 12px",
        borderRadius: 8,
        border: "1px solid var(--border, #30363d)",
        background: "var(--bg-secondary, #161b22)",
        color: "var(--text-secondary, #8b949e)",
        fontSize: 13,
        lineHeight: 1.45,
      }}
    >
      <span aria-hidden="true" style={{ flex: "0 0 auto", marginTop: 1 }}>
        ℹ️
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>{body}</span>
      {onDismiss ? (
        <button
          type="button"
          aria-label={`Dismiss ${title} intro`}
          onClick={onDismiss}
          style={{
            flex: "0 0 auto",
            background: "none",
            border: 0,
            color: "var(--text-muted, #6e7681)",
            cursor: "pointer",
            fontSize: 16,
            lineHeight: 1,
            padding: 2,
          }}
        >
          ×
        </button>
      ) : null}
    </aside>
  );
}
