/**
 * NotFoundPanel.tsx — Classified 404 Route Not Found panel for the application shell (SC-D2 / issue #1309).
 *
 * Preconditions:
 *  - `path` is the non-empty string representing the attempted route.
 *  - `onNavigateHome` is a callback invoked when the user selects the recovery action.
 *
 * Postconditions:
 *  - Renders inside the shell chrome (never a blank page or a silent redirect).
 *  - Displays the attempted URL, classified error badge (NOT_FOUND 404), and primary CTA.
 */
import React from "react";

export interface NotFoundPanelProps {
  /** The route pathname that failed to match any registered tab. */
  path: string;
  /** Recovery callback that navigates back to the default landing page (Staff Console). */
  onNavigateHome: () => void;
}

export function NotFoundPanel({
  path,
  onNavigateHome,
}: NotFoundPanelProps): React.ReactElement {
  return (
    <section
      role="region"
      aria-label="Route not found"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "64px 24px",
        maxWidth: "640px",
        margin: "0 auto",
        textAlign: "center",
      }}
    >
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "8px",
          padding: "4px 12px",
          borderRadius: "999px",
          background: "var(--accent-red-bg, rgba(248, 81, 73, 0.15))",
          color: "var(--accent-red, #f85149)",
          fontSize: "12px",
          fontWeight: 600,
          letterSpacing: "0.05em",
          marginBottom: "16px",
          border: "1px solid var(--accent-red, #f85149)",
        }}
      >
        <span>NOT_FOUND</span>
        <span>•</span>
        <span>404</span>
      </div>

      <h1
        style={{
          fontSize: "24px",
          fontWeight: 600,
          color: "var(--text-primary, #e6edf3)",
          marginBottom: "12px",
        }}
      >
        Page not found
      </h1>

      <p
        style={{
          fontSize: "14px",
          color: "var(--text-secondary, #8b949e)",
          lineHeight: 1.6,
          marginBottom: "20px",
        }}
      >
        The requested path{" "}
        <code
          style={{
            padding: "2px 6px",
            borderRadius: "4px",
            background: "var(--bg-secondary, #161b22)",
            border: "1px solid var(--border, #30363d)",
            color: "var(--text-primary, #e6edf3)",
            fontFamily: "monospace",
          }}
        >
          {path}
        </code>{" "}
        does not match any registered dashboard surface or has moved.
      </p>

      <p
        style={{
          fontSize: "13px",
          color: "var(--text-muted, #8b949e)",
          marginBottom: "28px",
        }}
      >
        Use the sidebar to navigate the four areas (Staff, Work, Fleet, Settings)
        or press <kbd style={{ padding: "2px 6px", borderRadius: "4px", border: "1px solid var(--border, #30363d)" }}>Ctrl+K</kbd> to search.
      </p>

      <button
        type="button"
        onClick={onNavigateHome}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "8px",
          padding: "8px 18px",
          borderRadius: "6px",
          background: "var(--accent-blue, #1f6feb)",
          color: "#ffffff",
          fontSize: "14px",
          fontWeight: 500,
          border: "none",
          cursor: "pointer",
          transition: "background 0.15s ease",
        }}
      >
        <span>Return to Staff Console</span>
      </button>
    </section>
  );
}
