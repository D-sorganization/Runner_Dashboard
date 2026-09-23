/**
 * PanelFrame.tsx — the card every Fleet Command panel renders in (#1233).
 *
 * Owns the shared header and the degradation contract so each panel only
 * renders its happy path (DRY): a route that 404s or answers
 * `available:false` shows "not available on this node" with the reason, a
 * failed read shows an error EmptyState with Retry, and a first load shows a
 * skeleton line. Once data exists, reloads keep the old content visible.
 */
import type { ReactNode } from "react";
import { EmptyState } from "../../primitives/EmptyState";
import { SkeletonCard } from "../../primitives/Skeleton";

export interface PanelFrameProps {
  title: string;
  testId: string;
  actions?: ReactNode;
  loading?: boolean;
  hasData?: boolean;
  error?: string | null;
  unavailable?: string | null;
  onRetry?: () => void;
  children?: ReactNode;
}

export function PanelFrame({
  title,
  testId,
  actions,
  loading = false,
  hasData = true,
  error = null,
  unavailable = null,
  onRetry,
  children,
}: PanelFrameProps) {
  let body: ReactNode = children;
  if (unavailable) {
    body = (
      <EmptyState
        title="Not available on this node"
        description={unavailable}
        onRetry={onRetry}
        data-testid={`${testId}-unavailable`}
      />
    );
  } else if (error && !hasData) {
    body = (
      <EmptyState
        variant="error"
        title={`Failed to load ${title.toLowerCase()}`}
        description={error}
        onRetry={onRetry}
      />
    );
  } else if (loading && !hasData) {
    body = (
      <div aria-busy="true">
        <SkeletonCard lines={3} />
      </div>
    );
  }
  return (
    <section className="glass-card staff-panel fleet-cmd__panel" data-testid={testId} aria-label={title}>
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">{title}</h3>
        {actions ? <div className="fleet-cmd__actions">{actions}</div> : null}
      </div>
      {error && hasData && !unavailable ? <p className="staff-error">{error}</p> : null}
      {body}
    </section>
  );
}

export default PanelFrame;
