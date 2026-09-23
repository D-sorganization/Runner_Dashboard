/**
 * Roster.tsx — one card per staff role from `GET /api/staff/roster` (#1198).
 *
 * Shows title, providers (with an "installed" badge from the roster's
 * `providers` availability map), schedule/window, active runs, budget and the
 * retired / dispatchable state. Presentational: the roster is fetched once by
 * StaffPage and shared with the Assign form (DRY).
 */
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { formatUsd, strategyLabel, type RoleSpec, type RosterResponse } from "./staffApi";

export interface RosterProps {
  roster: RosterResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onAssign?: (role: string) => void;
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
  onAssign,
}: {
  role: RoleSpec;
  providers: Record<string, boolean>;
  onAssign?: (role: string) => void;
}) {
  const state = role.retired ? "retired" : role.dispatchable ? "dispatchable" : "not dispatchable";
  const stateTone = role.retired ? "neutral" : role.dispatchable ? "success" : "warning";
  const strategy = strategyLabel(role);
  return (
    <article
      className={role.retired ? "staff-role staff-role--retired" : "staff-role"}
      data-testid={`role-card-${role.name}`}
      aria-label={`Role ${role.title}`}
    >
      <div className="staff-role__header">
        <h4 className="staff-role__title">{role.title}</h4>
        <Badge tone={stateTone} size="sm" className="staff-role__state">
          {state}
        </Badge>
      </div>
      {role.summary ? <p className="staff-role__summary">{role.summary}</p> : null}
      <dl className="staff-role__facts">
        <dt>Providers</dt>
        <dd className="staff-role__providers">
          {role.providers.length === 0 ? (
            <span className="staff-muted">none</span>
          ) : (
            role.providers.map((pid) => (
              <Badge
                key={pid}
                tone={providers[pid] ? "success" : "neutral"}
                size="sm"
                title={providers[pid] ? `${pid} is installed on this node` : `${pid} is not installed`}
              >
                {pid}
                {providers[pid] ? " · installed" : ""}
              </Badge>
            ))
          )}
        </dd>
        <dt>Schedule</dt>
        <dd>
          {role.schedule ?? <span className="staff-muted">manual</span>}
          {role.window ? ` (${role.window})` : ""}
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
      <div className="glass-card staff-panel" aria-busy="true">
        <p className="staff-muted">Loading roster...</p>
      </div>
    );
  }
  if (error && !roster) {
    return (
      <div className="glass-card staff-panel">
        <EmptyState variant="error" title="Failed to load roster" description={error} onRetry={onRetry} />
      </div>
    );
  }
  if (!roster || roster.roles.length === 0) {
    return (
      <div className="glass-card staff-panel">
        <EmptyState
          title="No staff roles found"
          description="Set STAFF_ROLES_DIR to the Repository_Management staff/roles directory on this node."
          onRetry={onRetry}
        />
      </div>
    );
  }
  return (
    <div className="glass-card staff-panel">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Roster</h3>
        <span className="staff-muted">
          {roster.machine} · {roster.active_runs} active
        </span>
      </div>
      <div className="staff-roster" data-testid="staff-roster">
        {roster.roles.map((role) => (
          <RoleCard key={role.name} role={role} providers={roster.providers} onAssign={onAssign} />
        ))}
      </div>
    </div>
  );
}

export default Roster;
