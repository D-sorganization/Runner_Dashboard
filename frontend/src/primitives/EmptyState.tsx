/**
 * EmptyState — reusable empty/error surface primitive (issue #837).
 *
 * Generalizes the best existing pattern (MaxwellChat's distinct idle-vs-
 * unreachable copy + Retry) into one primitive applied across Queue,
 * Remediation, Reports and Assessments. Two variants:
 *   - `variant="empty"`   — the call succeeded but there's nothing to show
 *                            (calm, informational);
 *   - `variant="error"`   — the call failed; shows operator guidance
 *                            (title + concrete action) plus a Retry button
 *                            instead of a raw status code.
 *
 * Accessibility: the error variant is a polite live region with role="status"
 * so screen readers announce the failure; the Retry button has an accessible
 * name. The optional icon is decorative (aria-hidden).
 *
 * LoD: flat typed props. Pair with {@link guidanceForFailure} to turn a
 * failure into `{ title, action }` before rendering.
 */
import React from "react";
import { TouchButton } from "./TouchButton";

export type EmptyStateVariant = "empty" | "error";

export interface EmptyStateProps {
  /** "empty" (nothing to show) or "error" (a call failed). Default "empty". */
  variant?: EmptyStateVariant;
  /** Headline. For errors, the operator-guidance title. */
  title: string;
  /** Supporting line. For errors, the concrete operator action. */
  description?: string;
  /** Decorative leading glyph (aria-hidden). */
  icon?: React.ReactNode;
  /** Retry handler — renders a Retry button when provided. */
  onRetry?: () => void;
  /** Override the retry button label (default "Retry"). */
  retryLabel?: string;
  /** Optional extra action (e.g. a deep-link to Credentials). */
  children?: React.ReactNode;
  /** Optional test id passthrough. */
  "data-testid"?: string;
}

export function EmptyState({
  variant = "empty",
  title,
  description,
  icon,
  onRetry,
  retryLabel = "Retry",
  children,
  "data-testid": testId,
}: EmptyStateProps): React.ReactElement {
  const isError = variant === "error";
  return (
    <div
      // Errors are announced politely; empty states are static informational.
      role={isError ? "status" : undefined}
      aria-live={isError ? "polite" : undefined}
      data-testid={testId}
      data-variant={variant}
      className="empty-state"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: "32px 20px",
        textAlign: "center",
        color: "var(--text-muted)",
      }}
    >
      {icon ? (
        <div
          aria-hidden="true"
          style={{
            fontSize: 28,
            lineHeight: 1,
            color: isError ? "var(--accent-red)" : "var(--text-muted)",
          }}
        >
          {icon}
        </div>
      ) : null}
      <div
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: isError ? "var(--accent-red)" : "var(--text-secondary)",
        }}
      >
        {title}
      </div>
      {description ? (
        <div style={{ fontSize: 13, maxWidth: 420, color: "var(--text-muted)" }}>
          {description}
        </div>
      ) : null}
      {(onRetry || children) && (
        <div
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
            justifyContent: "center",
            marginTop: 4,
          }}
        >
          {onRetry ? (
            <TouchButton
              aria-label={retryLabel}
              onClick={onRetry}
              variant="primary"
              style={{ fontSize: 12, minHeight: 34, padding: "4px 14px" }}
            >
              {retryLabel}
            </TouchButton>
          ) : null}
          {children}
        </div>
      )}
    </div>
  );
}
