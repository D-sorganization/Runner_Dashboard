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
import { useStaffQuota } from "../../hooks/useStaffQueries";
import { errorMessage, QUOTA_CEILING_PERCENT, quotaRows, type QuotaReport } from "./staffApi";

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

export function PlanQuota() {
  const { data, error } = useStaffQuota();
  return <PlanQuotaView report={data ?? null} error={error ? errorMessage(error) : null} />;
}
