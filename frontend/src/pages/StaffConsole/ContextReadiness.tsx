/**
 * ContextReadiness.tsx — context-pane sections that show what the backend reported
 * about a role: readiness, providers, offline nodes, runs and work items (#1804).
 *
 * Each section says when its source is still loading or failed, rather than
 * rendering an empty list or a green state that nobody reported.
 */
import React from "react";
import type {
  ActiveRunSummary,
  ProviderReadiness,
  RecentWorkItemSummary,
  RoleDetail,
  SourceState,
} from "./contextTypes";
import type { RosterStatus } from "./types";

const READINESS_LABELS: Record<RosterStatus, string> = {
  idle: "Ready on this node",
  working: "Working",
  needs_you: "Needs you",
  unavailable: "Cannot run now",
  held: "Held",
  unknown: "Readiness unknown",
  invalid: "Invalid role",
  retired: "Retired",
};

const PROVIDER_LABELS: Record<ProviderReadiness, string> = {
  installed: "installed",
  not_installed: "not installed",
  unknown: "unknown",
};

const PROVIDER_DOT: Record<ProviderReadiness, string> = {
  installed: "context-provider-dot--signed-in",
  not_installed: "context-provider-dot--signed-out",
  unknown: "context-provider-dot--unknown",
};

/** "Loading …" or "… unavailable: reason" for a source that has not answered; nothing when it has. */
export const SourceNote: React.FC<{ state?: SourceState; label: string }> = ({ state, label }) => {
  if (!state || state === "ready") return null;
  const text = state === "loading" ? `Loading ${label.toLowerCase()}…` : `${label} unavailable: ${state.error}`;
  return (
    <div className="context-empty-text" role={state === "loading" ? "status" : undefined}>
      {text}
    </div>
  );
};

export const RoleReadinessSection: React.FC<{ role: RoleDetail }> = ({ role }) => {
  const readiness = role.readiness;
  const offline = role.offline_nodes ?? [];
  return (
    <div>
      {readiness && (
        <div className="context-role-readiness" data-testid="context-role-readiness" data-status={readiness.status}>
          {READINESS_LABELS[readiness.status]}
          {readiness.reason && readiness.reason !== readiness.status ? ` — ${readiness.reason}` : ""}
        </div>
      )}
      <div className="context-section-label">Providers</div>
      <SourceNote state={role.sources?.providers} label="Provider availability" />
      <div className="context-providers-list">
        {role.providers.map((p) => (
          <span
            key={p.name}
            className="context-provider-chip"
            data-testid={`context-provider-${p.name}`}
            data-readiness={p.readiness}
          >
            <span className={`context-provider-dot ${PROVIDER_DOT[p.readiness]}`} aria-hidden="true" />
            <span>
              {p.name} · {PROVIDER_LABELS[p.readiness]}
            </span>
          </span>
        ))}
      </div>
      <div className="context-empty-text">Sign-in state is not reported by this node.</div>
      <SourceNote state={role.sources?.nodes} label="Node status" />
      {offline.length > 0 && (
        <div className="context-empty-text" data-testid="context-offline-nodes">
          Offline: {offline.join(", ")} — providers and runs there are unknown.
        </div>
      )}
    </div>
  );
};

export const RunList: React.FC<{ runs?: ActiveRunSummary[]; state?: SourceState; testId?: string }> = ({
  runs,
  state,
  testId,
}) => (
  <div className="context-runs-list" data-testid={testId}>
    <SourceNote state={state} label="Runs" />
    {runs?.map((r) => (
      <div key={r.id} className="context-run-row">
        <span className="context-run-id">{r.id}</span>
        <span className="context-run-status">{r.status}</span>
      </div>
    ))}
    {state === "ready" && !runs?.length && <div className="context-empty-text">None</div>}
  </div>
);

export const WorkItemList: React.FC<{ items?: RecentWorkItemSummary[]; state?: SourceState }> = ({ items, state }) => (
  <div>
    <SourceNote state={state} label="Work items" />
    {items?.map((wi) => (
      <div key={wi.id} className="context-work-item">
        <span className="context-work-item-id">{wi.id}</span>: <span>{wi.title}</span> ({wi.state})
      </div>
    ))}
    {(state === undefined || state === "ready") && !items?.length && <div className="context-empty-text">None</div>}
  </div>
);
