/**
 * Roster.tsx — one card per staff role from `GET /api/staff/roster` (#1198).
 *
 * Shows title, providers (with an "installed" badge from the roster's
 * `providers` availability map, or "outdated" when `provider_versions` says the
 * CLI is below its minimum, #1680), schedule/window, active runs, budget and the
 * retired / dispatchable state. Presentational: the roster is fetched once by
 * StaffPage and shared with the Assign form (DRY).
 */
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { formatRoleWindow } from "../StaffConsole/rosterUtils";
import { formatUsd, strategyLabel, type RoleSpec, type RosterResponse } from "./staffApi";

export interface RosterProps {
  roster: RosterResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onAssign?: (role: string) => void;
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

function budgetLabel(role: RoleSpec): string {
  const parts: string[] = [];
  if (role.budget.usd_per_run != null) parts.push(`${formatUsd(role.budget.usd_per_run)}/run`);
  if (role.budget.usd_per_day != null) parts.push(`${formatUsd(role.budget.usd_per_day)}/day`);
  return parts.length ? parts.join(" · ") : "no budget cap";
}

export function RoleCard({
  role,
  providers,
  versions,
  onAssign,
}: {
  role: RoleSpec;
  providers: Record<string, boolean>;
  versions?: RosterResponse["provider_versions"];
  onAssign?: (role: string) => void;
}) {
  const isInvalid = role.valid === false;
  const state = isInvalid
    ? "invalid"
    : role.retired
      ? "retired"
      : role.dispatchable
        ? "dispatchable"
        : "not dispatchable";
  const stateTone = isInvalid
    ? "danger"
    : role.retired
      ? "neutral"
      : role.dispatchable
        ? "success"
        : "warning";
  const strategy = strategyLabel(role);
  const errorMessage = role.error || (role.errors && role.errors.length > 0 ? role.errors.join("; ") : null);
  return (
    <article
      className={role.retired ? "staff-role staff-role--retired" : isInvalid ? "staff-role staff-role--invalid" : "staff-role"}
      data-testid={`role-card-${role.name}`}
      aria-label={`Role ${role.title}`}
    >
      <div className="staff-role__header">
        <h4 className="staff-role__title">{role.title}</h4>
        <Badge tone={stateTone} size="sm" className="staff-role__state">
          {state}
        </Badge>
      </div>
      {errorMessage ? (
        <p className="staff-role__error" style={{ color: "var(--status-danger, #dc2626)", fontSize: "0.85rem", marginTop: "0.25rem", display: "inline-flex", alignItems: "center", gap: 4 }}>
          <AlertTriangleIcon />
          <span>{errorMessage}</span>
        </p>
      ) : null}
      {role.summary ? <p className="staff-role__summary">{role.summary}</p> : null}
      <dl className="staff-role__facts">
        <dt>Providers</dt>
        <dd className="staff-role__providers">
          {role.providers.length === 0 ? (
            <span className="staff-muted">none</span>
          ) : (
            role.providers.map((pid) => {
              // An installed CLI below its minimum version is refused at launch; say so here.
              const outdated = providers[pid] && versions?.[pid]?.outdated ? versions[pid] : null;
              if (outdated) {
                return (
                  <Badge key={pid} tone="warning" size="sm" title={outdated.detail}>
                    {pid} · outdated {outdated.version}
                  </Badge>
                );
              }
              return (
                <Badge
                  key={pid}
                  tone={providers[pid] ? "success" : "neutral"}
                  size="sm"
                  title={providers[pid] ? `${pid} is installed on this node` : `${pid} is not installed`}
                >
                  {pid}
                  {providers[pid] ? " · installed" : ""}
                </Badge>
              );
            })
          )}
        </dd>
        <dt>Schedule</dt>
        <dd>
          {role.schedule ?? <span className="staff-muted">manual</span>}
          {role.window ? ` (${formatRoleWindow(role.window)})` : ""}
        </dd>
        <dt>Active runs</dt>
        <dd data-testid={`role-active-${role.name}`}>{role.active_runs}</dd>
        <dt>Budget</dt>
        <dd>{budgetLabel(role)}</dd>
        {strategy ? (
          <>
            <dt>Strategy</dt>
            <dd data-testid={`role-strategy-${role.name}`}>{strategy}</dd>
          </>
        ) : null}
        {role.repos.length > 0 ? (
          <>
            <dt>Repos</dt>
            <dd>{role.repos.join(", ")}</dd>
          </>
        ) : null}
        {role.holds.length > 0 ? (
          <>
            <dt>Holds</dt>
            <dd>{role.holds.join(", ")}</dd>
          </>
        ) : null}
      </dl>
      {onAssign ? (
        <TouchButton
          className="staff-role__assign"
          onClick={() => onAssign(role.name)}
          disabled={!role.dispatchable || role.retired}
        >
          Assign
        </TouchButton>
      ) : null}
    </article>
  );
}

export function Roster({ roster, loading, error, onRetry, onAssign }: RosterProps) {
  if (loading && !roster) {
    return (
      <div className="staff-panel" aria-busy="true">
        <p className="staff-muted">Loading roster...</p>
      </div>
    );
  }
  if (error && !roster) {
    return (
      <div className="staff-panel">
        <EmptyState variant="error" title="Failed to load roster" description={error} onRetry={onRetry} />
      </div>
    );
  }
  if (!roster || roster.roles.length === 0) {
    return (
      <div className="staff-panel">
        <EmptyState
          title="No staff roles found"
          description="Set STAFF_ROLES_DIR to the Repository_Management staff/roles directory on this node."
          onRetry={onRetry}
        />
      </div>
    );
  }
  return (
    <div className="staff-panel">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Roster</h3>
        <span className="staff-muted">
          {roster.machine} · {roster.active_runs} active
        </span>
      </div>
      <div className="staff-roster" data-testid="staff-roster">
        {roster.roles.map((role) => (
          <RoleCard
            key={role.name}
            role={role}
            providers={roster.providers}
            versions={roster.provider_versions}
            onAssign={onAssign}
          />
        ))}
      </div>
    </div>
  );
}

export default Roster;
