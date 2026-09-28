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
    >
      <svg
        className="intro-header__icon"
        aria-hidden="true"
        viewBox="0 0 16 16"
        width="14"
        height="14"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      >
        <circle cx="8" cy="8" r="6.25" />
        <path d="M8 7.25v3.5" />
        <path d="M8 5.1v.01" />
      </svg>
      <span className="intro-header__body">{body}</span>
      {onDismiss ? (
        <button
          type="button"
          className="intro-header__dismiss"
          aria-label={`Dismiss ${title} intro`}
          onClick={onDismiss}
        >
          ×
        </button>
      ) : null}
    </aside>
  );
}
