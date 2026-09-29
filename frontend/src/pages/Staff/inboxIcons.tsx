/** Shared icons and static path data for the Staff inbox panel (SC-C5).

Split from ``InboxPanel.tsx`` to keep that module under the repo's 500-line
soft cap. Exports components only (plus the ``KIND_ICON_PATHS`` path data).
*/
import React from "react";
import type { InboxSource, SourceStatus } from "./inboxTypes";

export function ChevronDownIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

export function ClipboardIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
    </svg>
  );
}

export function RefreshIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

export function CloseIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function AlertTriangleIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

export function CheckCircleIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

const KIND_ICON_PATHS: Record<InboxSource, React.ReactNode> = {
  approval: (
    <>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <polyline points="9 12 11 14 15 10" />
    </>
  ),
  escalation: (
    <>
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </>
  ),
  needs_input: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  auth_sign_in: (
    <>
      <circle cx="7.5" cy="15.5" r="4.5" />
      <path d="M10.7 12.3L21 2" />
      <path d="M16 7l3 3" />
    </>
  ),
  project_decision: (
    <>
      <path d="M4 22V4a1 1 0 0 1 1-1h11l-2 4 2 4H5" />
    </>
  ),
  board_proposal: (
    <>
      <path d="M9 18h6" />
      <path d="M10 22h4" />
      <path d="M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z" />
    </>
  ),
};

export function KindIcon({ source }: { source: InboxSource }) {
  return (
    <span className={`staff-inbox-row__icon staff-inbox-row__icon--${source}`} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {KIND_ICON_PATHS[source] ?? KIND_ICON_PATHS.needs_input}
      </svg>
    </span>
  );
}

export function InboxDegradedBanner({
  unavailableSources,
}: {
  unavailableSources: [string, SourceStatus][];
}) {
  if (unavailableSources.length === 0) return null;
  return (
    <div className="staff-inbox-panel__degraded-banner" role="alert" data-testid="inbox-degraded-banner">
      <AlertTriangleIcon className="staff-inbox-panel__degraded-icon" />
      <div className="staff-inbox-panel__degraded-body">
        <div className="staff-inbox-panel__degraded-headline">
          {`${unavailableSources.length} source${unavailableSources.length === 1 ? "" : "s"} unavailable: ${unavailableSources.map(([src]) => src).join(", ")}`}
        </div>
        <details className="staff-inbox-panel__degraded-disclosure">
          <summary>Details</summary>
          <ul className="staff-inbox-panel__degraded-list">
            {unavailableSources.map(([src, status]) => {
              const message = status.error || "Temporarily unavailable";
              return (
                <li key={src}>
                  <code>{src}</code>:{" "}
                  <span
                    className="staff-inbox-panel__degraded-error"
                    title={message}
                    data-testid={`inbox-degraded-error-${src}`}
                  >
                    {message}
                  </span>
                </li>
              );
            })}
          </ul>
        </details>
      </div>
    </div>
  );
}