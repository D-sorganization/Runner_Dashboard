/**
 * PlanQuota.tsx — how much of each subscription plan's windows is used (#1588).
 *
 * Reads `GET /api/staff/quota` once a minute. That route only reads local files,
 * so polling it spends no plan quota. Runs start only while a plan is under its
 * window ceiling (85 % unless the role sets `budget.max_window_percent`).
 * Orthogonal to the rest of the tab: a failure renders an inline notice.
 */
import { Badge } from "../../primitives/Badge";
import { TimeAgo } from "../../primitives/TimeAgo";
import { useStaffQuota, useStaffUsageSnapshot } from "../../hooks/useStaffQueries";
import { errorMessage, QUOTA_CEILING_PERCENT, quotaRows, type QuotaReport, type UsageSnapshotReport } from "./staffApi";

export interface PlanQuotaViewProps {
  report: QuotaReport | null;
  error: string | null;
}

export function PlanQuotaView({ report, error }: PlanQuotaViewProps) {
  if (error) return <p className="staff-muted">Plan quota unavailable: {error}</p>;
  if (!report) return <p className="staff-muted">Loading plan quota...</p>;
  const rows = quotaRows(report);
  return (
    <ul className="staff-board__alerts" aria-label="Plan quota">
      {rows.map((row) => (
        <li key={row.provider} data-testid={`plan-quota-${row.provider}`}>
          <Badge tone={row.tone} size="sm">
            {row.peak == null ? "—" : `${Math.round(row.peak)}%`}
          </Badge>{" "}
          <strong>{row.provider}</strong> {row.summary}
          {row.limitedUntil ? (
            <>
              {" "}
              · limited until <TimeAgo iso={row.limitedUntil} />
            </>
          ) : null}
        </li>
      ))}
      <li className="staff-muted">Runs pause at {QUOTA_CEILING_PERCENT}% of any window unless a role sets its own ceiling.</li>
    </ul>
  );
}

const STALE_SNAPSHOT_SECONDS = 2 * 60 * 60;

export interface UsageSnapshotViewProps {
  report: UsageSnapshotReport | null;
  error: string | null;
}

/** Fleet (Usage-Tracker) section (#3042): per-machine % used, red when stale. */
export function UsageSnapshotView({ report, error }: UsageSnapshotViewProps) {
  if (error) return <p className="staff-muted">Fleet usage unavailable: {error}</p>;
  if (!report) return <p className="staff-muted">Loading fleet usage...</p>;
  if (!report.checked_at) return <p className="staff-muted" data-testid="usage-snapshot-empty">No snapshot yet.</p>;

  const snapshotStale = report.snapshot_stale || (report.snapshot_age_seconds ?? 0) > STALE_SNAPSHOT_SECONDS;
  const ageMinutes = Math.round((report.snapshot_age_seconds ?? 0) / 60);

  return (
    <div data-testid="usage-snapshot">
      <p className="staff-muted">
        Snapshot {ageMinutes} min ago
        {snapshotStale ? (
          <>
            {" "}
            <Badge tone="danger" size="sm">
              stale
            </Badge>
          </>
        ) : null}
      </p>
      <ul className="staff-board__alerts" aria-label="Fleet usage (Usage-Tracker)">
        {report.providers.map((provider) => (
          <li key={provider.provider}>
            <Badge tone={provider.near_limit_flag ? "warning" : "neutral"} size="sm">
              {provider.quota_used_pct == null ? "—" : `${Math.round(provider.quota_used_pct)}%`}
            </Badge>{" "}
            <strong>{provider.label}</strong>
            {provider.machines.length > 0 ? (
              <ul>
                {provider.machines.map((machine) => (
                  <li key={machine.machine} data-testid={`usage-snapshot-${provider.provider}-${machine.machine}`}>
                    <Badge tone={machine.stale ? "danger" : "neutral"} size="sm">
                      {machine.used_percent == null ? "—" : `${Math.round(machine.used_percent)}%`}
                    </Badge>{" "}
                    {machine.machine} · <TimeAgo iso={machine.observed_at} />
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PlanQuota() {
  const { data, error } = useStaffQuota();
  const { data: snapshot, error: snapshotError } = useStaffUsageSnapshot();
  return (
    <>
      <PlanQuotaView report={data ?? null} error={error ? errorMessage(error) : null} />
      <UsageSnapshotView report={snapshot ?? null} error={snapshotError ? errorMessage(snapshotError) : null} />
    </>
  );
}
